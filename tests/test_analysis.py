from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ainews.ai import AIClient, UsageLedger, strict_schema
from ainews.config import Settings
from ainews.events import EventStore
from ainews.models import Candidate, DraftItem, Scores, SourceStatus
from ainews.quality import QualityError, check_coverage, filter_candidates
from ainews.timeutil import research_window

NOW = datetime(2026, 10, 13, 1, tzinfo=UTC)


def settings(**kwargs):
    return Settings(pricing={"gpt-5.4-mini": {"input": .75, "cached_input": .075, "output": 4.5}}, **kwargs)


def draft(**overrides):
    values = dict(event_key="acme:model-x", milestone="announcement", title="Acme发布模型X",
                  category="models", priority="P1", score=Scores(impact=4, novelty=4, relevance=4, confidence=4, follow_up=4),
                  confidence="high", occurred_at=None, is_followup=False, previous_event_id=None, new_information="",
                  summary="Acme官方发布了新的模型X和相关技术报告。", significance="能力升级。", analysis="可能改善研发流程。",
                  followup_questions=[], candidate_ids=["c1"], tags=["模型"], fact_status="FACT",
                  repository_url=None, technical_highlights=None, target_users=None, maturity_limitations=None)
    return DraftItem(**(values | overrides))


def cand(**overrides):
    values = dict(id="c1", source_id="s", title="Model X", url="https://example.com/model-x",
                  excerpt="Official announcement", published_at=NOW-timedelta(hours=1), time_precision="exact",
                  collected_at=NOW, region="asia", categories=["models"], verified=True)
    return Candidate(**(values | overrides))


def test_cross_day_url_and_event_dedup():
    store = EventStore({"version": 1, "events": {}, "reports": {}})
    event_id, previous, duplicate = store.match(draft(), [cand()])
    assert not duplicate
    store.remember(draft(), [cand()], event_id, "2026-10-12")
    assert store.match(draft(), [cand(url="https://example.com/model-x?utm_source=foo")])[2]
    assert store.match(draft(candidate_ids=["c2"]), [cand(id="c2", url="https://other.com/x")])[2]


def test_real_progress_requires_new_evidence_and_increment():
    store = EventStore({"version": 1, "events": {}, "reports": {}})
    event_id = store.match(draft(), [cand()])[0]
    store.remember(draft(), [cand()], event_id, "2026-10-12")
    followup = draft(milestone="general availability", is_followup=True, previous_event_id=event_id,
                     new_information="正式上线并公布价格。")
    result = store.match(followup, [cand(excerpt="New official general availability and pricing")])
    assert result[0] == event_id and result[1]["summary"] and not result[2]
    assert store.match(followup, [cand()])[2]
    with pytest.raises(QualityError):
        store.match(draft(is_followup=True, previous_event_id="missing", new_information="new"), [cand()])


def test_main_window_uses_last_success_and_date_boundary():
    last = NOW - timedelta(hours=26)
    state = {"reports": {"2026-10-12": {"window_end": last.isoformat()}}}
    assert research_window(NOW, state, date(2026, 10, 13)) == (last, NOW)
    start = NOW-timedelta(days=1)
    candidates = [cand(id="start", published_at=start), cand(id="end", published_at=NOW),
                  cand(id="future", published_at=NOW+timedelta(seconds=1)),
                  cand(id="unknown", published_at=None, time_precision="unknown")]
    accepted, rejected = filter_candidates(candidates, start, NOW)
    assert [c.id for c in accepted] == ["start"]
    assert len(rejected) == 3


def test_date_only_near_boundary_rejected_instead_of_guessing():
    start, end = NOW-timedelta(days=1), NOW
    # Publication day interval overlaps the exclusive upper cutoff.
    accepted, rejected = filter_candidates([cand(published_at=NOW.replace(hour=0), time_precision="date")], start, end)
    assert not accepted and rejected


def test_schema_is_strict_and_bad_outputs_rejected():
    schema = strict_schema(DraftItem)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    with pytest.raises(ValidationError):
        draft(priority="P9")
    with pytest.raises(ValidationError):
        draft(summary="")


def test_source_failure_blocks_publication():
    status = SourceStatus(source_id="s", name="s", kind="web_search", region="global", categories=["models"],
                          status="failed", checked_at=NOW)
    with pytest.raises(QualityError):
        check_coverage([status], Settings())


def test_budget_and_cached_token_recording():
    ledger = UsageLedger(settings())
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100, input_tokens_details=SimpleNamespace(cached_tokens=200))
    response = SimpleNamespace(id="r", usage=usage, output=[])
    ledger.record("gpt-5.4-mini", response, "edit")
    assert ledger.entries[0]["cached_tokens"] == 200
    assert ledger.total == pytest.approx((800*.75 + 200*.075 + 100*4.5)/1_000_000)
    with pytest.raises(QualityError):
        ledger.reserve("unknown-model", "x", 100)


def test_api_failure_is_redacted_and_bounded():
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("secret sk-sensitive http://secret")
    sdk = SimpleNamespace(responses=SimpleNamespace(create=create))
    client = AIClient(settings(attempts=2), sdk=sdk, sleep=lambda _: None)
    with pytest.raises(QualityError) as exc:
        client.request(DraftItem, "system", {}, "gpt-5.4-mini", "edit")
    assert "secret" not in str(exc.value)
    assert len(calls) == 1  # Permanent errors are never retried.
    assert client.ledger.entries[-1]["billing_unknown"]


def test_timeout_reservation_stops_retries_before_budget_exceeded():
    import httpx
    import openai
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    sdk = SimpleNamespace(responses=SimpleNamespace(create=create))
    client = AIClient(settings(attempts=3, max_estimated_cost_usd=.05), sdk=sdk, sleep=lambda _: None)
    with pytest.raises(QualityError, match="budget"):
        client.request(DraftItem, "system", {}, "gpt-5.4-mini", "edit")
    assert len(calls) == 1 and client.ledger.budget_used > .04
