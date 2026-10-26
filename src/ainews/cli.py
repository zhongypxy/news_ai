import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from .collectors import Collector
from .config import load_config
from .models import Report
from .net import SafeHTTP
from .pipeline import run_daily
from .publish import Publisher, RepositoryLock, atomic_write
from .quality import QualityError
from .render import Renderer
from .work import ResearchPacket, prepare_work, publish_work, work_evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description="Global AI daily: real collection, verified reports and offline archives")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root containing config/prompts/templates")
    sub = parser.add_subparsers(dest="command", required=True)
    daily = sub.add_parser("run", help="Collect, analyze, verify and publish (requires OPENAI_API_KEY)")
    daily.add_argument("--date", type=date.fromisoformat, help="Historical report cutoff: YYYY-MM-DD at 09:00 UTC+8")
    collect = sub.add_parser("collect", help="Real public-source diagnostics without API charges or a news report")
    collect.add_argument("--source", help="Only inspect this configured public source")
    sub.add_parser("check", help="Validate configuration and regenerate empty/history index; no paid API calls")
    sub.add_parser("schema", help="Export final report JSON Schema")
    sub.add_parser("work-prepare", help="Collect public sources and prepare Work research context; no API Key")
    work = sub.add_parser("work-publish", help="Verify and publish an actual Work research packet; no API Key")
    work.add_argument("--bundle", type=Path, required=True)
    work.add_argument("--research", type=Path, required=True)
    evidence = sub.add_parser("work-evidence", help="Read original short evidence before Work editorial writing")
    evidence.add_argument("--bundle", type=Path, required=True)
    evidence.add_argument("--discoveries", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "run":
            result = run_daily(root, day=args.date)
            print(result)
        elif args.command == "work-prepare":
            path = prepare_work(root)
            print(f"Work research bundle: {path}" if path else "already_published")
        elif args.command == "work-publish":
            print(publish_work(root, args.bundle, args.research))
        elif args.command == "work-evidence":
            print(work_evidence(root, args.bundle, args.discoveries))
        elif args.command == "schema":
            atomic_write(root/"config/report.schema.json", json.dumps(Report.model_json_schema(), ensure_ascii=False, indent=2).encode("utf-8"))
            atomic_write(root/"config/work.schema.json", json.dumps(ResearchPacket.model_json_schema(), ensure_ascii=False, indent=2).encode("utf-8"))
            print("Report JSON Schema exported")
        elif args.command == "check":
            settings, sources = load_config(root)
            renderer = Renderer(root/"templates")
            publisher = Publisher(root, renderer)
            with RepositoryLock(root):
                publisher.recover()
                state = publisher.load_state()
                for day in state["reports"]:
                    publisher.completed(day)
                atomic_write(root/"index.html", renderer.index(state["reports"]).encode("utf-8"))
            print(f"Configuration valid: {len(sources)} sources; {settings.mini_model} / {settings.major_model}; {len(state['reports'])} reports")
        else:
            settings, sources = load_config(root)
            if args.source and not any(s.id == args.source for s in sources):
                raise QualityError("Unknown source ID")
            http = SafeHTTP(timeout=settings.http_timeout, attempts=settings.attempts)
            collector = Collector(http)
            statuses, count = [], 0
            try:
                for source in sources:
                    if args.source and source.id != args.source:
                        continue
                    items, status = collector.collect(source, datetime.now(UTC))
                    count += len(items)
                    statuses.append(status.model_dump(mode="json"))
                    print(f"{source.id}: {status.status}, collected {len(items)}" + (f", {status.error}" if status.error else ""), flush=True)
                atomic_write(root/"logs/source-probe.json", json.dumps({"mode": "real_public_collection_only",
                             "checked_at": datetime.now(UTC).isoformat(), "candidates": count, "source_status": statuses,
                             "limitations": ["AIHOT candidates only; original verification and Work editing remain pending. No paid API calls."
                                 if settings.discovery_provider == "aihot" else
                                 "No Web Search, editorial analysis or report generated; requires API Key"]},
                             ensure_ascii=False, indent=2).encode("utf-8"))
                if not any(s["status"] == "ok" for s in statuses):
                    raise QualityError("No configured public source collected successfully")
            finally:
                http.close()
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, QualityError) else type(exc).__name__
        print("Failed: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
