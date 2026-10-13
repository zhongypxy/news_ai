from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_analysis import NOW, cand, draft, settings

from ainews.editor import Editor
from ainews.events import EventStore
from ainews.models import EditorialBatch, Review, Source, Synthesis
from ainews.quality import QualityError


class FakeAI:
    """Explicit test double, never used by production commands."""
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.ledger = SimpleNamespace(entries=[], total=0)

    def request(self, schema, instructions, payload, model, purpose, **kwargs):
        self.calls.append((schema, model, purpose, payload))
        return self.answers.pop(0)


def context():
    return {"RUN_DATE": "2026-10-13", "WINDOW_START": NOW.replace(day=12), "WINDOW_END": NOW,
            "HISTORY": {}, "WATCHLIST": [], "SOURCE_STATUS": []}


def setup(answers):
    ai = FakeAI(answers)
    editor = Editor(ai, "trusted prompt", settings(), [Source(id="s", kind="rss", name="Official",
                    url="https://example.com/rss", allowed_domains=["example.com"])])
    return ai, editor, EventStore({"version": 1, "events": {}, "reports": {}})


def review(**kwargs):
    return Review(**(dict(approved=True, supported_candidate_ids=["c1"], issues=[], independent_source_count=1,
                          occurred_at_supported=True, new_information_supported=True) | kwargs))


def test_candidate_accounting_blocks_invented_source_ids():
    ai, editor, events = setup([EditorialBatch(news_items=[draft(candidate_ids=["fake"])], rejected_candidate_ids=[])])
    with pytest.raises(QualityError, match="accounting"):
        editor.edit([cand()], events, context())


def test_major_factual_failure_is_fatal():
    result = EditorialBatch(news_items=[draft()], rejected_candidate_ids=[])
    ai, editor, events = setup([result, result, review(approved=False, issues=["Unsupported price"])])
    with pytest.raises(QualityError, match="Major event factual"):
        editor.edit([cand()], events, context())
    assert [call[1] for call in ai.calls] == ["gpt-5.4-mini", "gpt-5.4", "gpt-5.4"]


def test_minor_news_can_be_rejected_without_creating_fake_content():
    result = EditorialBatch(news_items=[draft(priority="P2")], rejected_candidate_ids=[])
    ai, editor, events = setup([result, review(approved=False, issues=["unsupported"])])
    items, synthesis = editor.edit([cand()], events, context())
    assert items == [] and synthesis.top_stories == []


def test_unknown_synthesis_event_is_rejected():
    result = EditorialBatch(news_items=[draft(priority="P2")], rejected_candidate_ids=[])
    ai, editor, events = setup([result, review(), Synthesis(executive_summary="本期出现了模型升级等行业动态。",
                             top_stories=["fake"], trend_signals=[])])
    with pytest.raises(QualityError, match="synthesis"):
        editor.edit([cand()], events, context())


def test_new_day_repeat_event_is_filtered():
    item = draft(priority="P2")
    result = EditorialBatch(news_items=[item], rejected_candidate_ids=[])
    ai, editor, events = setup([result, review()])
    event_id = events.match(item, [cand()])[0]
    events.remember(item, [cand()], event_id, "2026-10-12")
    original = deepcopy(events.state)
    items, synthesis = editor.edit([cand()], events, context())
    assert not items and editor.duplicates == 1 and events.state == original
