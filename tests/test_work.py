import json
from datetime import timedelta

import pytest
from test_analysis import NOW, cand, draft
from test_editor import review
from test_pipeline import TestCollector, project

from ainews.models import EditorialBatch, Synthesis, SynthesisReview
from ainews.quality import QualityError
from ainews.work import ResearchPacket, SearchCheck, WorkBackend, prepare_work, publish_work


def test_prepare_does_not_need_key_or_claim_search_success(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    root = project(tmp_path)
    path = prepare_work(root, now=NOW, collector_factory=TestCollector)
    bundle = json.loads(path.read_text('utf-8'))
    assert bundle['mode'] == 'chatgpt_work'
    assert bundle['context']['WINDOW_END'] == NOW.isoformat()
    assert bundle['candidates'] and bundle['state_sha256']
    assert not list((root/'reports').glob('*.html'))


def packet():
    return ResearchPacket(bundle_sha256='a'*64, evidence_sha256='b'*64, discoveries=[], search_checks=[],
        editorial=EditorialBatch(news_items=[draft(priority='P2')], rejected_candidate_ids=[]),
        reviews={'acme:model-x': review()},
        synthesis=Synthesis(executive_summary='Acme发布了模型X，具体情况需要持续关注。', top_stories=[], trend_signals=[]),
        synthesis_review=SynthesisReview(approved=True, unsupported_claims=[]), limitations=[])


def test_work_backend_is_explicit_research_import_and_enforces_review():
    research = packet()
    backend = WorkBackend(research)
    result = backend.request(EditorialBatch, '', {'CANDIDATES': [cand().model_dump()]}, '', 'candidate_edit')
    assert result.news_items[0].title == research.editorial.news_items[0].title
    assert backend.request(type(review()), '', {'DRAFT': draft().model_dump()}, '', 'fact_verification').approved
    with pytest.raises(QualityError, match='unsupported'):
        backend.request(EditorialBatch, '', {}, '', 'unknown')


def test_work_failed_search_never_publishes(tmp_path):
    root = project(tmp_path)
    path = prepare_work(root, now=NOW, collector_factory=TestCollector)
    research = packet()
    from ainews.publish import digest
    research.bundle_sha256 = digest(path.read_bytes())
    research.search_checks = [SearchCheck(source_id='search_models', queries=['model news'],
        result_urls=[], status='failed', note='search unavailable', checked_at=NOW)]
    research_path = root/'logs/research.json'
    research_path.write_text(research.model_dump_json(), 'utf-8')
    with pytest.raises(QualityError, match='search'):
        publish_work(root, path, research_path, now=NOW, collector_factory=TestCollector)
    assert not list((root/'reports').glob('*.html'))
    diagnostics = list((root/'logs').glob('work-*/diagnostics.json'))
    assert json.loads(diagnostics[0].read_text('utf-8'))['status'] == 'failed'


def test_work_bundle_cannot_be_reused_after_state_changed(tmp_path):
    root = project(tmp_path)
    path = prepare_work(root, now=NOW, collector_factory=TestCollector)
    research_path = root/'logs/research.json'
    from ainews.publish import digest
    research = packet()
    research.bundle_sha256 = digest(path.read_bytes())
    research_path.write_text(research.model_dump_json(), 'utf-8')
    state_path = root/'state/events.json'
    state = {'version': 1, 'events': {}, 'reports': {}}
    state['events']['new-event'] = {}
    state_path.write_text(json.dumps(state), 'utf-8')
    with pytest.raises(QualityError, match='state changed'):
        publish_work(root, path, research_path, now=NOW)


@pytest.mark.parametrize('case', ['publish', 'omit_verified', 'changed_evidence'])
def test_work_real_import_pipeline_and_idempotent_repeat(tmp_path, case):
    from ainews.publish import digest
    root = project(tmp_path)
    path = prepare_work(root, now=NOW, collector_factory=TestCollector)
    bundle = json.loads(path.read_text('utf-8'))
    research = packet()
    research.bundle_sha256 = digest(path.read_bytes())
    item = draft(priority='P2', candidate_ids=['openai'])
    research.editorial = EditorialBatch(news_items=[item],
        rejected_candidate_ids=[c['id'] for c in bundle['candidates'] if c['id'] != 'openai'])
    evidence_path = path.with_name('evidence.json')
    evidence_path.write_text(json.dumps({'verified': bundle['candidates'], 'rejected': []}), 'utf-8')
    research.evidence_sha256 = digest(evidence_path.read_bytes())
    if case == 'omit_verified':
        research.editorial.rejected_candidate_ids.remove('nvidia')
    if case == 'changed_evidence':
        evidence_path.write_text('{}', 'utf-8')
    research.reviews = {item.event_key: review(supported_candidate_ids=['openai'])}
    research.synthesis.top_stories = [item.event_key]
    research.search_checks = [SearchCheck(source_id=s['id'], queries=s['queries'], result_urls=[],
        status='ok', note='Real searches represented by explicit test fixture', checked_at=NOW)
        for s in bundle['search_sources']]
    research_path = path.with_name('research.json')
    research_path.write_text(research.model_dump_json(), 'utf-8')
    if case != 'publish':
        with pytest.raises(QualityError, match='evidence accounting' if case == 'omit_verified' else 'manifest hash'):
            publish_work(root, path, research_path, now=NOW, collector_factory=TestCollector)
        assert not list((root/'reports').glob('*.html'))
        return
    assert publish_work(root, path, research_path, now=NOW, collector_factory=TestCollector) == 'published'
    report = json.loads((root/'data/2026-10-13.json').read_text('utf-8'))
    assert report['usage'][0]['mode'] == 'chatgpt_work'
    assert report['usage'][0]['input_tokens'] is None
    assert report['top_stories'][0].startswith('evt_')
    assert publish_work(root, path, research_path, now=NOW) == 'already_published'
    assert json.loads(path.with_name('diagnostics.json').read_text('utf-8'))['status'] == 'already_published'
    assert publish_work(root, path, research_path, now=NOW+timedelta(hours=12, minutes=1)) == 'already_published'
    assert publish_work(root, path, research_path, now=NOW+timedelta(days=1)) == 'already_published'


def test_work_search_provenance_blocks_invented_links(tmp_path):
    from ainews.publish import digest
    from ainews.work import Discovery
    root = project(tmp_path)
    path = prepare_work(root, now=NOW, collector_factory=TestCollector)
    bundle = json.loads(path.read_text('utf-8'))
    research = packet()
    research.bundle_sha256 = digest(path.read_bytes())
    research.search_checks = [SearchCheck(source_id=s['id'], queries=s['queries'], result_urls=[],
        status='ok', note='No eligible news; actual search represented by fixture', checked_at=NOW)
        for s in bundle['search_sources']]
    research.discoveries = [Discovery(source_id='search_models', title='Invented model', url='https://example.com/fake')]
    research_path = path.with_name('research.json')
    research_path.write_text(research.model_dump_json(), 'utf-8')
    with pytest.raises(QualityError, match='provenance'):
        publish_work(root, path, research_path, now=NOW)
