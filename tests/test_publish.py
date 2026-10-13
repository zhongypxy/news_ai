import json
from datetime import timedelta
from pathlib import Path

import jsonschema
import pytest
from test_analysis import NOW, draft

from ainews.models import Citation, Coverage, NewsItem, Quality, Report
from ainews.publish import Publisher, RepositoryLock
from ainews.quality import QualityError
from ainews.render import Renderer

ROOT = Path(__file__).resolve().parents[1]


def report(title="已核验模型新闻"):
    item = NewsItem(**draft(title=title).model_dump(), event_id="evt_test", published_at=NOW-timedelta(hours=1),
                    time_precision="exact", collected_at=NOW, is_backfill=False, previous_summary=None,
                    sources=[Citation(candidate_id="c1", url="https://example.com/news", name="Official", source_type="rss",
                                      verification_status="verified", published_at=NOW-timedelta(hours=1), time_precision="exact",
                                      collected_at=NOW, publication_basis="source_metadata")], weighted_score=4,
                    independent_source_count=1, verification_limitations=[])
    return Report(report_date=NOW.date(), window_start=NOW-timedelta(days=1), window_end=NOW,
                  backfill_start=NOW-timedelta(days=3), generated_at=NOW, timezone="Asia/Singapore",
                  executive_summary="本期已完成公开来源检索和事实核验。", top_stories=["evt_test"], news_items=[item],
                  open_source_items=[], trend_signals=[], tracking_updates=[],
                  coverage_report=Coverage(source_status=[], categories_checked=[], categories_missing=[],
                                           regions_checked=[], limitations=[]),
                  quality_report=Quality(status="passed", candidates_collected=10, candidates_verified=5,
                                         published_count=1, duplicates_removed=0, rejected_count=5, limitations=[]),
                  usage=[], estimated_cost_usd=0, prompt_sha256="abc")


def state():
    return {"version": 1, "events": {}, "reports": {}}


def publisher(tmp_path):
    return Publisher(tmp_path, Renderer(ROOT / "templates"))


def test_offline_html_is_self_contained_and_escaped():
    html = Renderer(ROOT / "templates").report(report('<script>alert("x")</script>'))
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html
    assert "fetch(" not in html and "cdn" not in html.lower()
    assert 'id="search"' in html and 'id="theme"' in html
    assert "https://example.com/news" in html
    assert "FACT" in html and "ANALYSIS" in html


def test_final_report_json_schema():
    r = report()
    jsonschema.validate(json.loads(r.model_dump_json()), Report.model_json_schema())
    malformed = json.loads(r.model_dump_json())
    malformed["news_items"][0]["priority"] = "fake"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(malformed, Report.model_json_schema())


def test_publish_is_idempotent_and_protects_history(tmp_path):
    pub = publisher(tmp_path)
    pub.publish(report(), state())
    before = (tmp_path / "reports/2026-10-13.html").read_bytes()
    assert pub.completed("2026-10-13")
    with pytest.raises(QualityError):
        pub.publish(report("另一个版本的标题"), pub.load_state())
    assert (tmp_path / "reports/2026-10-13.html").read_bytes() == before
    assert pub.load_state()["reports"]["2026-10-13"]["window_end"] == NOW.isoformat()


def test_crash_recovery_commits_matching_transaction(tmp_path):
    pub = publisher(tmp_path)
    def fail_after_daily(stage):
        if stage == "daily_written":
            raise OSError("Test interruption")
    with pytest.raises(OSError):
        pub.publish(report(), state(), checkpoint=fail_after_daily)
    assert (tmp_path / "state/.pending.json").exists()
    pub.recover()
    assert pub.completed("2026-10-13")
    assert not (tmp_path / "state/.pending.json").exists()


def test_damaged_archive_is_not_recreated_or_hidden(tmp_path):
    pub = publisher(tmp_path)
    pub.publish(report(), state())
    (tmp_path / "data/2026-10-13.json").write_text("corrupted", encoding="utf-8")
    with pytest.raises(QualityError, match="integrity"):
        pub.completed("2026-10-13")


def test_process_lock_rejects_concurrent_writer(tmp_path):
    with RepositoryLock(tmp_path):
        with pytest.raises(QualityError, match="already"):
            with RepositoryLock(tmp_path):
                pass


def test_no_partial_final_file_when_creation_interrupted(tmp_path, monkeypatch):
    import os
    pub = publisher(tmp_path)
    original = os.link
    def interruption(src, dst):
        # The temporary file is complete before the final path appears.
        assert Path(src).read_bytes().rstrip().endswith(b"</html>")
        assert not Path(dst).exists()
        raise OSError("Interrupted before atomic link")
    monkeypatch.setattr(os, "link", interruption)
    with pytest.raises(OSError):
        pub.publish(report(), state())
    assert not (tmp_path/"reports/2026-10-13.html").exists()
    monkeypatch.setattr(os, "link", original)
    pub.recover()
    assert pub.completed("2026-10-13")


def test_recovery_cannot_roll_back_changed_event_state(tmp_path):
    pub = publisher(tmp_path)
    def interrupt(stage):
        if stage == "daily_written":
            raise OSError("interrupted")
    with pytest.raises(OSError):
        pub.publish(report(), state(), checkpoint=interrupt)
    (tmp_path/"state/events.json").write_text(json.dumps({"version": 1, "events": {"other": {}}, "reports": {}}), encoding="utf-8")
    with pytest.raises(QualityError, match="Pending"):
        pub.recover()
