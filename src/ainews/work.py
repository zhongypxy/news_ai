"""Import real research performed by ChatGPT Work; never calls a paid model API."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field

from .collectors import Collector, candidate
from .config import load_config
from .editor import Editor
from .events import EventStore, identity
from .models import (
    Candidate,
    EditorialBatch,
    Quality,
    Report,
    Review,
    SourceStatus,
    StrictModel,
    Synthesis,
    SynthesisReview,
)
from .net import SafeHTTP, normalize_url, validate_url
from .pipeline import balanced_pool
from .publish import Publisher, RepositoryLock, atomic_write, digest
from .quality import QualityError, check_coverage, filter_candidates, require_verified
from .render import Renderer
from .timeutil import research_window


class Discovery(StrictModel):
    source_id: str
    title: str = Field(min_length=3, max_length=500)
    url: str
    region: Literal['north_america', 'asia', 'europe', 'global'] = 'global'


class SearchCheck(StrictModel):
    source_id: str
    queries: list[str] = Field(min_length=1)
    result_urls: list[str]
    status: Literal['ok', 'failed']
    note: str = Field(min_length=3, max_length=1500)
    checked_at: AwareDatetime


class ResearchPacket(StrictModel):
    bundle_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    discoveries: list[Discovery] = Field(max_length=80)
    search_checks: list[SearchCheck]
    editorial: EditorialBatch
    reviews: dict[str, Review]
    synthesis: Synthesis
    synthesis_review: SynthesisReview
    limitations: list[str]


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, default=str).encode('utf-8'))


def prepare_work(root, now=None, collector_factory=Collector):
    root, now = Path(root), now or datetime.now(UTC)
    settings, sources = load_config(root)
    day = now.astimezone(ZoneInfo(settings.timezone)).date()
    with RepositoryLock(root):
        publisher = Publisher(root, Renderer(root/'templates'))
        publisher.recover()
        publisher.validate_history()
        if publisher.completed(day.isoformat()):
            return None
        state = publisher.load_state()
        start, end = research_window(now, state, day)
        backfill = min(start, end-timedelta(hours=settings.backfill_hours))
        http = SafeHTTP(timeout=settings.http_timeout, attempts=settings.attempts)
        statuses, items = [], []
        try:
            collector = collector_factory(http)
            for source in sources:
                if source.kind == 'web_search':
                    continue
                pool, status = collector.collect(source, now)
                items.extend(pool)
                statuses.append(status)
                print(f'{source.id}: {status.status}, collected {len(pool)}', flush=True)
        finally:
            http.close()
        path = root/'logs'/f'work-{day}-{uuid4().hex[:8]}'/'bundle.json'
        write_json(path, {
            'mode': 'chatgpt_work', 'state_sha256': digest(json.dumps(state, sort_keys=True).encode()),
            'prompt_sha256': digest((root/'prompts/daily_research.md').read_bytes()),
            'context': {'RUN_DATE': day.isoformat(), 'WINDOW_START': start.isoformat(),
                        'WINDOW_END': end.isoformat(), 'BACKFILL_START': backfill.isoformat(),
                        'HISTORY': EventStore(state).history(end, settings.tracking_days),
                        'WATCHLIST': settings.watchlist},
            'candidates': [c.model_dump(mode='json') for c in items],
            'source_status': [s.model_dump(mode='json') for s in statuses],
            'search_sources': [s.model_dump(mode='json') for s in sources if s.kind == 'web_search'],
            'limitations': ['公开采集已执行；Work 实时搜索、原文核验和研究编辑尚未执行。'],
        })
        return path


class WorkBackend:
    """Read explicit editorial/review artifacts authored by Work, with no synthetic answers."""
    def __init__(self, packet):
        self.packet = packet

    def request(self, schema, instructions, payload, model, purpose, **kwargs):
        if purpose == 'candidate_edit':
            ids = {c['id'] for c in payload['CANDIDATES']}
            drafts = [d for d in self.packet.editorial.news_items if set(d.candidate_ids) <= ids]
            return EditorialBatch(news_items=drafts,
                rejected_candidate_ids=[c for c in self.packet.editorial.rejected_candidate_ids if c in ids])
        if purpose == 'major_event_edit':
            return EditorialBatch(news_items=[payload['DRAFT']], rejected_candidate_ids=[])
        if purpose == 'fact_verification':
            key = payload['DRAFT']['event_key']
            if key not in self.packet.reviews:
                raise QualityError('Work research lacks explicit factual review')
            return self.packet.reviews[key]
        if purpose == 'report_synthesis':
            result = self.packet.synthesis.model_copy(deep=True)
            ids = {n['event_key']: n['event_id'] for n in payload['NEWS_ITEMS']}
            result.top_stories = [ids.get(key, key) for key in result.top_stories]
            for trend in result.trend_signals:
                trend.event_ids = [ids.get(key, key) for key in trend.event_ids]
            return result
        if purpose == 'synthesis_verification':
            return self.packet.synthesis_review
        raise QualityError('Work backend unsupported editorial operation: '+purpose)


def work_evidence(root, bundle_path, discoveries_path=None):
    """Read original sources before Work writes summaries. Short evidence stays in ignored logs."""
    root = Path(root)
    settings, sources = load_config(root)
    by_source = {s.id: s for s in sources}
    bundle = json.loads(Path(bundle_path).read_text('utf-8'))
    ctx = bundle['context']
    start, end = [datetime.fromisoformat(ctx[k]) for k in ['BACKFILL_START', 'WINDOW_END']]
    items = [Candidate.model_validate(c) for c in bundle['candidates']]
    if discoveries_path:
        for raw in json.loads(Path(discoveries_path).read_text('utf-8')):
            d = Discovery.model_validate(raw)
            if d.source_id not in by_source or by_source[d.source_id].kind != 'web_search':
                raise QualityError('Discovery must reference configured search source')
            c = candidate(by_source[d.source_id], d.title, d.url, None, '', datetime.now(UTC), 'search_unverified')
            c.region = d.region
            items.append(c)
    eligible, rejected = filter_candidates(items, start, end)
    pool = balanced_pool(eligible+[c for c in items if c.published_at is None], settings.max_candidates)
    http = SafeHTTP(timeout=settings.http_timeout, attempts=settings.attempts)
    verified = []
    try:
        collector = Collector(http)
        for c in pool:
            try:
                collector.verify(c, by_source[c.source_id])
                valid, failures = filter_candidates([c], start, end)
                verified.extend(valid)
                rejected.extend(failures)
                print(f'{c.id}: {"verified" if valid else "outside_window"}', flush=True)
            except Exception as exc:
                rejected.append({'candidate_id': c.id, 'reason': type(exc).__name__})
                print(f'{c.id}: {type(exc).__name__}', flush=True)
    finally:
        http.close()
    path = Path(bundle_path).with_name('evidence.json')
    write_json(path, {'verified': [c.model_dump(mode='json') for c in verified], 'rejected': rejected})
    return path


def publish_work(root, bundle_path, research_path, now=None, collector_factory=Collector):
    root, now = Path(root), now or datetime.now(UTC)
    logdir = Path(bundle_path).parent
    diagnostics = {'mode': 'chatgpt_work', 'started_at': now.isoformat(), 'status': 'running', 'rejections': []}
    http = None
    try:
        settings, sources = load_config(root)
        packet = ResearchPacket.model_validate_json(Path(research_path).read_bytes())
        diagnostics['search_checks'] = [c.model_dump(mode='json') for c in packet.search_checks]
        bundle_bytes = Path(bundle_path).read_bytes()
        if digest(bundle_bytes) != packet.bundle_sha256:
            raise QualityError('Work research bundle hash mismatch')
        bundle = json.loads(bundle_bytes)
        ctx = bundle['context']
        day = ctx['RUN_DATE']
        start, end, backfill = [datetime.fromisoformat(ctx[k]) for k in ['WINDOW_START', 'WINDOW_END', 'BACKFILL_START']]
        with RepositoryLock(root):
            publisher = Publisher(root, Renderer(root/'templates'))
            publisher.recover()
            if publisher.completed(day):
                diagnostics['status'] = 'already_published'
                return 'already_published'
            if day != now.astimezone(ZoneInfo(settings.timezone)).date().isoformat() or end > now or now-end > timedelta(hours=12):
                raise QualityError('Work research bundle is stale or future-dated; prepare again')
            state = publisher.load_state()
            if digest(json.dumps(state, sort_keys=True).encode()) != bundle['state_sha256']:
                raise QualityError('Event state changed since Work bundle preparation')
            if digest((root/'prompts/daily_research.md').read_bytes()) != bundle['prompt_sha256']:
                raise QualityError('Research prompt changed since Work bundle preparation')
            source_map = {s.id: s for s in sources}
            searches = {s.id: s for s in sources if s.enabled and s.kind == 'web_search'}
            checks = {s.source_id: s for s in packet.search_checks}
            if set(checks) != set(searches) or len(checks) != len(packet.search_checks):
                raise QualityError('Work search checks must cover all configured searches exactly once')
            statuses = [SourceStatus.model_validate(s) for s in bundle['source_status']]
            for key, source in searches.items():
                check = checks[key]
                if check.checked_at < end or check.checked_at > now:
                    raise QualityError('Invalid Work search check time')
                for url in check.result_urls:
                    validate_url(url)
                statuses.append(SourceStatus(source_id=key, name=source.name, kind='web_search',
                    region=source.region, categories=source.categories, status=check.status,
                    fetched=len(check.result_urls), error=check.note if check.status == 'failed' else None,
                    checked_at=check.checked_at))
            diagnostics['source_status'] = [s.model_dump(mode='json') for s in statuses]
            coverage = check_coverage(statuses, settings)
            items = [Candidate.model_validate(c) for c in bundle['candidates']]
            for d in packet.discoveries:
                if d.source_id not in searches or normalize_url(d.url) not in {normalize_url(u) for u in checks[d.source_id].result_urls}:
                    raise QualityError('Work discovery URL lacks actual search provenance')
                c = candidate(source_map[d.source_id], d.title, d.url, None, '', now, 'search_unverified')
                c.region = d.region
                items.append(c)
            by_id = {c.id: c for c in items}
            selected = {cid for d in packet.editorial.news_items for cid in d.candidate_ids}
            accounted = selected | set(packet.editorial.rejected_candidate_ids)
            if accounted - set(by_id) or selected & set(packet.editorial.rejected_candidate_ids):
                raise QualityError('Work editorial contains unknown or rejected evidence IDs')
            if len({identity(d.event_key) for d in packet.editorial.news_items}) != len(packet.editorial.news_items):
                raise QualityError('Work must consolidate same-event drafts before import')
            evidence_path = Path(bundle_path).with_name('evidence.json')
            if not evidence_path.exists():
                raise QualityError('Work evidence manifest missing; run work-evidence first')
            evidence_bytes = evidence_path.read_bytes()
            if digest(evidence_bytes) != packet.evidence_sha256:
                raise QualityError('Work evidence manifest hash mismatch; review current evidence')
            evidence_manifest = json.loads(evidence_bytes)
            evidence_items = [Candidate.model_validate(c) for c in evidence_manifest['verified']]
            evidence_ids = {c.id for c in evidence_items}
            if len(evidence_ids) != len(evidence_items) or accounted != evidence_ids or not all(c.verified for c in evidence_items):
                raise QualityError('Work evidence accounting incomplete; select or reject every verified candidate')
            diagnostics['evidence_sha256'] = packet.evidence_sha256
            diagnostics['evidence_stage_rejections'] = evidence_manifest['rejected']
            http = SafeHTTP(timeout=settings.http_timeout, attempts=settings.attempts)
            collector = collector_factory(http)
            verified = []
            for cid in sorted(accounted):
                c = by_id[cid]
                try:
                    collector.verify(c, source_map[c.source_id])
                    valid, rejected = filter_candidates([c], backfill, end)
                    verified.extend(valid)
                    diagnostics['rejections'].extend(rejected)
                except Exception as exc:
                    diagnostics['rejections'].append({'candidate_id': cid, 'reason': type(exc).__name__})
            if selected - {c.id for c in verified}:
                raise QualityError('Work selected evidence failed original source/time verification')
            require_verified(verified, len(accounted), settings)
            for status in statuses:
                status.eligible = sum(c.source_id == status.source_id for c in verified)
            coverage.regions_checked = sorted({c.region for c in verified})
            events = EventStore(state)
            editor_settings = settings.model_copy(update={'batch_size': max(1, len(verified))})
            editor = Editor(WorkBackend(packet), (root/'prompts/daily_research.md').read_text('utf-8'), editor_settings, sources)
            context = {**ctx, 'WINDOW_START': start, 'WINDOW_END': end}
            news, synthesis = editor.edit(verified, events, context)
            limitations = packet.limitations + [
                '由 ChatGPT Work 完成检索、编辑与再次事实审查；不是独立模型或人工审稿。',
                '未调用 OpenAI API；Work 不向本程序提供实际 token、所用模型和订阅消耗，记录为未知。',
                '代码核验原文可访问性、发布时间、来源引用、结构与历史；无法证明全部语义判断正确。']
            report = Report(report_date=day, window_start=start, window_end=end, backfill_start=backfill,
                generated_at=now, timezone=settings.timezone, executive_summary=synthesis.executive_summary,
                top_stories=synthesis.top_stories, news_items=[n for n in news if n.category != 'opensource'],
                open_source_items=[n for n in news if n.category == 'opensource'], trend_signals=synthesis.trend_signals,
                tracking_updates=[n.event_id for n in news if n.is_followup], coverage_report=coverage,
                quality_report=Quality(status='passed', candidates_collected=len(items), candidates_verified=len(verified),
                    published_count=len(news), duplicates_removed=editor.duplicates,
                    rejected_count=len(diagnostics['rejections'])+len(editor.rejections), limitations=limitations),
                usage=[{'mode': 'chatgpt_work', 'model': 'Work（应用选择，未公开）', 'purpose': '搜索、研究编辑、事实审查',
                        'input_tokens': None, 'output_tokens': None, 'estimated_cost_usd': None, 'billing_unknown': True}],
                estimated_cost_usd=0, prompt_sha256=bundle['prompt_sha256'])
            publisher.publish(report, events.state)
            diagnostics['status'] = 'published'
            return 'published'
    except Exception as exc:
        diagnostics['status'] = 'failed'
        diagnostics['error'] = str(exc) if isinstance(exc, QualityError) else type(exc).__name__
        raise
    finally:
        if http:
            http.close()
        write_json(logdir/'diagnostics.json', diagnostics)
