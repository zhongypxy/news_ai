import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from test_analysis import NOW, cand
from test_editor import FakeAI

from ainews.models import EditorialBatch, SourceStatus
from ainews.pipeline import run_daily
from ainews.quality import QualityError

ROOT = Path(__file__).resolve().parents[1]


def project(tmp_path):
    for name in ["config", "templates", "prompts"]:
        shutil.copytree(ROOT/name, tmp_path/name)
    return tmp_path


class TestCollector:
    __test__ = False
    def __init__(self, http, search=None, fail=False):
        self.fail = fail

    def collect(self, source, now):
        status = SourceStatus(source_id=source.id, name=source.name, kind=source.kind, region=source.region,
                              categories=source.categories, status="failed" if self.fail else "ok", checked_at=now)
        if source.kind == "web_search" or self.fail:
            return [], status
        c = cand(id=source.id, source_id=source.id, url=f"https://example.com/{source.id}",
                 categories=source.categories, region=source.region)
        status.fetched = 1
        return [c], status

    def verify(self, item, source):
        return item


def test_offline_pipeline_publish_then_repeat_without_api(tmp_path):
    root = project(tmp_path)
    ids = ["openai", "google_ai", "microsoft", "nvidia", "huggingface", "qwen", "vllm", "langgraph", "anthropic", "rea"]
    ai = FakeAI([EditorialBatch(news_items=[], rejected_candidate_ids=ids)])
    ai.search = lambda *args: []
    ai.search_notes = []
    result = run_daily(root, now=NOW, ai_factory=lambda *args, **kwargs: ai, collector_factory=TestCollector)
    assert result == "published"
    def forbidden(*args, **kwargs):
        raise AssertionError("Idempotent rerun must not call the API")
    assert run_daily(root, now=NOW+timedelta(minutes=77), ai_factory=forbidden) == "already_published"
    record = json.loads((root/"state/events.json").read_text("utf-8"))["reports"]["2026-10-13"]
    assert record["window_end"] == NOW.isoformat()


def test_failed_collection_preserves_diagnostics_and_no_report(tmp_path):
    root = project(tmp_path)
    ai = FakeAI([])
    ai.search = lambda *args: []
    ai.search_notes = []
    with pytest.raises(QualityError):
        run_daily(root, now=NOW, ai_factory=lambda *args, **kwargs: ai,
                  collector_factory=lambda *args, **kwargs: TestCollector(*args, fail=True, **kwargs))
    assert not list((root/"reports").glob("*.html"))
    diagnostics = list((root/"logs").glob("*/diagnostics.json"))
    assert len(diagnostics) == 1
    payload = json.loads(diagnostics[0].read_text("utf-8"))
    assert payload["status"] == "failed" and payload["source_status"]


def test_missing_api_key_fails_cleanly(tmp_path, monkeypatch):
    root = project(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(QualityError, match="OPENAI_API_KEY"):
        run_daily(root, now=NOW)
    assert not list((root/"reports").glob("*.html"))


def test_future_manual_date_rejected(tmp_path):
    root = project(tmp_path)
    with pytest.raises(QualityError, match="future"):
        run_daily(root, day=(NOW+timedelta(days=1)).date(), now=NOW)


def test_unknown_page_date_is_resolved_from_original_source(tmp_path):
    root = project(tmp_path)
    seen = []
    class DateResolvingCollector(TestCollector):
        def collect(self, source, now):
            items, status = super().collect(source, now)
            if source.id == "anthropic":
                items[0].published_at = None
                items[0].time_precision = "unknown"
            return items, status
        def verify(self, item, source):
            seen.append(source.id)
            if source.id == "anthropic":
                item.published_at = NOW-timedelta(hours=1)
                item.time_precision = "exact"
            return item
    ids = ["openai", "google_ai", "microsoft", "nvidia", "huggingface", "qwen", "vllm", "langgraph", "anthropic", "rea"]
    ai = FakeAI([EditorialBatch(news_items=[], rejected_candidate_ids=ids)])
    ai.search = lambda *args: []
    ai.search_notes = []
    assert run_daily(root, now=NOW, ai_factory=lambda *a, **kw: ai, collector_factory=DateResolvingCollector) == "published"
    assert "anthropic" in seen
