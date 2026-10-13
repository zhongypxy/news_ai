import hashlib
import json
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .ai import AIClient
from .collectors import Collector
from .config import load_config
from .editor import Editor
from .events import EventStore
from .models import Quality, Report, SourceStatus
from .net import SafeHTTP
from .publish import Publisher, RepositoryLock, atomic_write
from .quality import FatalAPIError, QualityError, check_coverage, filter_candidates, require_verified
from .render import Renderer
from .timeutil import report_window, research_window


def balanced_pool(items, limit):
    """Round-robin across publishers so a large feed cannot displace all other sources."""
    groups = {}
    for item in sorted(items, key=lambda c: c.published_at or datetime.min.replace(tzinfo=UTC), reverse=True):
        groups.setdefault(item.source_id, deque()).append(item)
    selected, seen = [], set()
    while groups and len(selected) < limit:
        for key in list(groups):
            item = groups[key].popleft()
            if item.url not in seen:
                seen.add(item.url)
                selected.append(item)
            if not groups[key]:
                del groups[key]
            if len(selected) >= limit:
                break
    return selected


def run_daily(root, day=None, now=None, ai_factory=AIClient, collector_factory=Collector):
    root = Path(root)
    now = now or datetime.now(UTC)
    settings, sources = load_config(root)
    local_day = now.astimezone(ZoneInfo(settings.timezone)).date()
    day = day or local_day
    if day > local_day:
        raise QualityError("Cannot generate a report for a future date")
    with RepositoryLock(root):
        renderer = Renderer(root/"templates")
        publisher = Publisher(root, renderer)
        publisher.recover()
        if publisher.completed(day.isoformat()):
            return "already_published"
        state = publisher.load_state()
        if any(d > day.isoformat() for d in state["reports"]):
            raise QualityError("Historical backdating after a newer report is forbidden")
        logdir = root/"logs"/f"{day}-{now.strftime('%H%M%S')}-{uuid4().hex[:8]}"
        logdir.mkdir(parents=True, exist_ok=True)
        diagnostics = {"report_date": day.isoformat(), "started_at": now.isoformat(), "status": "running",
                       "source_status": [], "rejections": [], "limitations": []}
        ai = None
        http = SafeHTTP(timeout=settings.http_timeout, attempts=settings.attempts)
        def save():
            if ai:
                diagnostics["usage"] = ai.ledger.entries
                diagnostics["estimated_cost_usd"] = ai.ledger.total
            atomic_write(logdir/"diagnostics.json", json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
        def stage(text):
            print(text, flush=True)
            with (logdir/"run.log").open("a", encoding="utf-8") as handle:
                handle.write(datetime.now(UTC).isoformat()+" "+text+"\n")
            save()
        try:
            save()
            end = now if day == local_day else report_window(day, settings.timezone, settings.report_hour)[1]
            start, end = research_window(end, state, day)
            # Include missed main-window time even after an outage longer than 72 hours.
            backfill_start = min(start, end-timedelta(hours=settings.backfill_hours))
            events = EventStore(state)
            context = {"RUN_DATE": day.isoformat(), "WINDOW_START": start, "WINDOW_END": end,
                       "HISTORY": events.history(end, settings.tracking_days), "WATCHLIST": settings.watchlist,
                       "SOURCE_STATUS": []}
            prompt = (root/"prompts/daily_research.md").read_text("utf-8")
            diagnostics["window_start"], diagnostics["window_end"] = start.isoformat(), end.isoformat()
            ai = ai_factory(settings, sink=lambda entries: save())
            collector = collector_factory(http, search=lambda source, stamp: ai.search(source, stamp, context))
            all_items, statuses = [], []
            # Third-round backfill is explicitly last, after the six broad category checks.
            for source in sorted(sources, key=lambda s: s.id == "search_backfill"):
                context["CANDIDATE_TITLES"] = [{"id": c.id, "title": c.title, "categories": c.categories} for c in all_items][-80:]
                try:
                    items, status = collector.collect(source, now)
                except FatalAPIError as exc:
                    status = SourceStatus(source_id=source.id, name=source.name, kind=source.kind,
                                          region=source.region, categories=source.categories, status="failed",
                                          error=str(exc), checked_at=now)
                    statuses.append(status)
                    diagnostics["source_status"] = [s.model_dump(mode="json") for s in statuses]
                    raise
                all_items.extend(items)
                statuses.append(status)
                context["SOURCE_STATUS"] = [s.model_dump(mode="json") for s in statuses]
                diagnostics["source_status"] = context["SOURCE_STATUS"]
                stage(f"Source {source.id}: {status.status}, collected {status.fetched}")
            coverage = check_coverage(statuses, settings)
            # Pre-filter precisely dated entries; unknown/search dates are resolved from original pages.
            eligible, rejected = filter_candidates(all_items, backfill_start, end)
            unresolved = [c for c in all_items if c.published_at is None]
            pool = balanced_pool(eligible+unresolved, settings.max_candidates)
            source_map = {s.id: s for s in sources}
            verified = []
            for item in pool:
                try:
                    collector.verify(item, source_map[item.source_id])
                    in_window, new_rejections = filter_candidates([item], backfill_start, end)
                    verified.extend(in_window)
                    rejected.extend(new_rejections)
                except Exception as exc:
                    rejected.append({"candidate_id": item.id, "reason": "original_verification_failed",
                                     "error_type": type(exc).__name__})
            for status in statuses:
                status.eligible = sum(c.source_id == status.source_id for c in verified)
                status.rejected = sum(r["candidate_id"] in {c.id for c in all_items if c.source_id == status.source_id} for r in rejected)
            diagnostics["source_status"] = [s.model_dump(mode="json") for s in statuses]
            diagnostics["rejections"] = rejected
            diagnostics["search_checks"] = ai.search_notes
            stage(f"Verified original sources: {len(verified)} of {len(pool)}")
            require_verified(verified, len(pool), settings)
            coverage.regions_checked = sorted({c.region for c in verified})
            context["SOURCE_STATUS"] = diagnostics["source_status"]
            editor = Editor(ai, prompt, settings, sources)
            items, synthesis = editor.edit(verified, events, context)
            limitations = []
            if not state["reports"]:
                limitations.append("首次运行，没有历史日报和连续开源活跃度快照")
            if len(eligible) > settings.max_candidates:
                limitations.append(f"候选超出 {settings.max_candidates} 条预算上限，按来源轮询筛选；存在漏报风险")
            if end-start > timedelta(hours=settings.backfill_hours):
                limitations.append("主窗口跨越多日；RSS/API 保留长度与搜索索引可能无法补全所有历史消息")
            for check in ai.search_notes:
                limitations.extend(check["limitations"])
            limitations.append("事件语义匹配和事实支持判断包含 AI 推断；代码校验不能证明所有事实绝对正确")
            if any(entry.get("billing_unknown") for entry in ai.ledger.entries):
                limitations.append("部分 API 请求失败或超时，实际费用可能高于已记录估算值")
            report = Report(report_date=day, window_start=start, window_end=end, backfill_start=backfill_start,
                            generated_at=datetime.now(UTC), timezone=settings.timezone,
                            executive_summary=synthesis.executive_summary, top_stories=synthesis.top_stories,
                            news_items=[i for i in items if i.category != "opensource"],
                            open_source_items=[i for i in items if i.category == "opensource"],
                            trend_signals=synthesis.trend_signals, tracking_updates=[i.event_id for i in items if i.is_followup],
                            coverage_report=coverage, quality_report=Quality(status="passed", candidates_collected=len(all_items),
                            candidates_verified=len(verified), published_count=len(items), duplicates_removed=editor.duplicates,
                            rejected_count=len(rejected)+len(editor.rejections), limitations=limitations), usage=ai.ledger.entries,
                            estimated_cost_usd=ai.ledger.total, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
            diagnostics["rejections"].extend(editor.rejections)
            stage("All quality gates passed; publishing report")
            publisher.publish(report, events.state)
            diagnostics["status"] = "published"
            diagnostics["published_count"] = len(items)
            stage(f"Published {day.isoformat()}: {len(items)} events")
            return "published"
        except Exception as exc:
            diagnostics["status"] = "failed"
            diagnostics["error_type"] = type(exc).__name__
            diagnostics["error"] = str(exc) if isinstance(exc, QualityError) else "Unexpected error; see error type"
            stage("Report failed: " + diagnostics["error_type"] + "; diagnostics saved")
            raise
        finally:
            http.close()
