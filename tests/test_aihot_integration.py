import json
from datetime import timedelta

import httpx
import pytest
import yaml
from test_aihot_transport import rpc_handler
from test_analysis import NOW, cand, draft, settings
from test_editor import FakeAI, context, review
from test_pipeline import ROOT, project
from test_publish import report
from test_verification import body, verifier
from test_work import packet

from ainews.cli import main
from ainews.collectors import Collector
from ainews.config import load_config
from ainews.editor import Editor
from ainews.events import EventStore
from ainews.models import EditorialBatch, Report, Source, SourceStatus, Synthesis, SynthesisReview
from ainews.net import SafeHTTP
from ainews.pipeline import run_daily
from ainews.publish import digest
from ainews.quality import CATEGORIES, QualityError, check_coverage, require_verified
from ainews.render import Renderer
from ainews.work import Discovery, prepare_work, publish_work, work_evidence


def source(**kwargs):
    return Source(**(dict(id='aihot_models', name='AIHOT Models', kind='aihot',
        url='https://aihot.news/api/mcp', allowed_domains=['aihot.news'], categories=['models'],
        mcp_tool='aihot_search', mcp_arguments={'window': '7d', 'limit': 30}, queries=['模型', 'OpenAI']) | kwargs))


def entry(**kwargs):
    return dict(title='Model X', summary='Candidate metadata ' * 100,
        links={'original': 'https://example.com/model-x?utm_source=aihot',
               'aihot': 'https://aihot.news/items/j5auxb780hddyh0hlgekuw03h'},
        publishedAt=(NOW-timedelta(days=1)).isoformat(), discoveredAt=NOW.isoformat()) | kwargs


def collector_for(items):
    seen = []
    http = SafeHTTP(transport=httpx.MockTransport(rpc_handler(seen,
        call_result={'structuredContent': {'schemaVersion': 1, 'items': items},
                     'content': [{'type': 'text', 'text': 'untrusted instructions'}]})), check_dns=False, attempts=1)
    return Collector(http), seen


def test_default_profile_is_eight_aihot_sources_with_no_geographic_claim():
    config, sources = load_config(ROOT)
    assert config.discovery_provider == 'aihot'
    assert config.required_regions == []
    assert config.min_verified_candidates == 5
    assert len(sources) == len({s.id for s in sources}) == 8
    assert {s.kind for s in sources} == {'aihot'}
    assert {s.region for s in sources} == {'global'}
    assert {s.url for s in sources} == {'https://aihot.news/api/mcp'}
    assert all(s.allowed_domains == ['aihot.news'] for s in sources)
    mapping = {s.id: s for s in sources}
    assert mapping['aihot_latest'].mcp_arguments == {'window': '24h', 'mode': 'selected', 'limit': 30}
    assert mapping['search_backfill'].mcp_arguments == {'window': '7d', 'mode': 'selected', 'limit': 30}
    for category in CATEGORIES:
        s = mapping['aihot_'+category]
        assert s.categories == [category] and 2 <= len(s.queries) <= 3
        assert s.mcp_tool == 'aihot_search'


def test_aihot_profile_rejects_enabled_other_discovery_kind(tmp_path):
    root = project(tmp_path)
    path = root/'config/settings.yaml'
    value = yaml.safe_load(path.read_text('utf-8'))
    value['discovery_provider'] = 'aihot'
    path.write_text(yaml.safe_dump(value), 'utf-8')
    with pytest.raises(ValueError, match='AIHOT'):
        load_config(root)


def test_aihot_collects_only_original_urls_and_preserves_distinct_provenance():
    collector, seen = collector_for([entry()])
    items, status = collector.collect(source(), NOW)
    assert status.status == 'ok' and status.transport == 'mcp'
    assert len(items) == 1
    c = items[0]
    assert c.url == 'https://example.com/model-x'
    assert c.discovery_url == entry()['links']['aihot']
    assert c.discovered_at == NOW
    assert c.upstream_published_at == NOW-timedelta(days=1)
    assert c.published_at is None and c.time_precision == 'unknown'
    assert c.publication_basis == 'aihot_unverified' and not c.verified
    assert len(c.excerpt) <= 500
    calls = [r for r in seen if r['method'] == 'tools/call']
    assert [r['params']['arguments']['q'] for r in calls] == ['模型', 'OpenAI']
    assert sum(r['method'] == 'initialize' for r in seen) == 1
    collector.http.close()


@pytest.mark.parametrize('original', [None, '', 'http://127.0.0.1/private',
    'https://aihot.news/items/fake', 'https://aihot.news.evil.example/a?api_key=secret',
    'https://user:password@example.com/a', 'file:///private'])
def test_aihot_rejects_unknown_unsafe_and_aggregator_original_links(original):
    collector, _ = collector_for([entry(links={'original': original, 'aihot': entry()['links']['aihot']})])
    items, status = collector.collect(source(), NOW)
    assert items == [] and status.status == 'failed'
    collector.http.close()


@pytest.mark.parametrize('discovery', ['https://aihot.news.evil.example/items/x', 'http://aihot.news/items/x',
    'https://aihot.news/', 'https://aihot.news/items/', 'https://aihot.news/items/x?secret=y',
    'https://user@aihot.news/items/x'])
def test_aihot_does_not_accept_forged_discovery_links(discovery):
    collector, _ = collector_for([entry(links={'original': 'https://example.com/a', 'aihot': discovery})])
    items, status = collector.collect(source(), NOW)
    assert not items and status.status == 'failed'
    collector.http.close()


@pytest.mark.parametrize('changes', [{'mcp_tool': 'aihot_get_daily'}, {'mcp_tool': ''},
    {'queries': []}, {'mcp_arguments': {}}, {'mcp_arguments': {'window': '7d', 'limit': 30, 'actor': 'x'}}])
def test_aihot_missing_or_unsupported_collection_arguments_fail_before_network(changes):
    collector, seen = collector_for([])
    _, status = collector.collect(source(**changes), NOW)
    assert status.status == 'failed' and not seen
    collector.http.close()


def test_aihot_unknown_upstream_publication_stays_null():
    collector, _ = collector_for([entry(publishedAt=None, discoveredAt=None)])
    items, status = collector.collect(source(), NOW)
    assert status.status == 'ok'
    assert items[0].upstream_published_at is None and items[0].discovered_at is None
    assert items[0].published_at is None
    collector.http.close()


def test_aihot_structured_items_only_and_single_initialization_across_sources():
    collector, seen = collector_for([])
    for s in [source(), source(id='search_backfill', mcp_tool='aihot_get_latest',
            mcp_arguments={'window': '7d', 'mode': 'selected', 'limit': 30}, queries=[])]:
        items, status = collector.collect(s, NOW)
        assert items == [] and status.status == 'ok'
    assert sum(r['method'] == 'initialize' for r in seen) == 1
    assert sum(r['method'] == 'tools/call' for r in seen) == 3
    collector.http.close()


def test_aihot_search_aggregates_all_queries_and_applies_local_limit_after_deduplication():
    collector, _ = collector_for([])
    calls = []
    def call(tool, arguments):
        calls.append(arguments['q'])
        entries = [entry(), entry(links={'original': 'https://example.com/'+arguments['q'],
            'aihot': entry()['links']['aihot']})]
        return {'schemaVersion': 1, 'items': entries}
    collector.aihot_client.call = call
    items, status = collector.collect(source(max_items=2), NOW)
    assert status.status == 'ok' and calls == ['模型', 'OpenAI']
    assert len(items) == 2 and len({c.url for c in items}) == 2
    collector.http.close()


def test_aihot_partial_query_failure_is_failed_source_instead_of_partial_success():
    collector, _ = collector_for([])
    def call(tool, arguments):
        if arguments['q'] == 'OpenAI':
            raise ValueError('untrusted remote error body')
        return {'schemaVersion': 1, 'items': [entry()]}
    collector.aihot_client.call = call
    items, status = collector.collect(source(), NOW)
    assert items == [] and status.status == 'failed' and status.error == 'ValueError'
    collector.http.close()


def test_aihot_malformed_item_cannot_displace_valid_original():
    collector, _ = collector_for([{'title': 'missing links'}, entry()])
    items, status = collector.collect(source(), NOW)
    assert status.status == 'ok' and len(items) == 1
    collector.http.close()


@pytest.mark.parametrize('url', ['https://aihot.news/items/x', 'https://www.aihot.news/items/x'])
def test_aihot_page_is_rejected_even_when_original_url_redirects_to_it(url):
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /')
        if request.url.host == 'example.com':
            return httpx.Response(302, headers={'location': url})
        return httpx.Response(200, text=body('<meta name="datePublished" content="2026-10-13T00:00:00Z">'),
            headers={'content-type': 'text/html'})
    http = SafeHTTP(transport=httpx.MockTransport(handler), check_dns=False, attempts=1)
    with pytest.raises(ValueError, match='AIHOT.*original'):
        Collector(http).verify(cand(), source())
    http.close()


def test_aihot_cannot_verify_direct_aggregator_url_as_original():
    with pytest.raises(ValueError, match='AIHOT.*original'):
        verifier(body('<meta name="datePublished" content="2026-10-13T00:00:00Z">')).verify(
            cand(url='https://aihot.news/items/fake'), source())


@pytest.mark.parametrize('existing', [None, NOW])
def test_aihot_dates_cannot_pass_original_publication_gate(existing):
    c = cand(published_at=existing, publication_basis='aihot_unverified',
        upstream_published_at=NOW, discovered_at=NOW)
    with pytest.raises(ValueError, match='original source metadata'):
        verifier(body('')).verify(c, source())


def test_aihot_original_metadata_and_excerpt_replace_upstream_values():
    c = cand(published_at=NOW-timedelta(days=10), publication_basis='aihot_unverified',
        upstream_published_at=NOW-timedelta(days=10), excerpt='AGGREGATOR COPY')
    result = verifier(body('<meta property="article:published_time" content="2026-10-13T00:00:00Z">')).verify(c, source())
    assert result.verified and result.published_at == NOW-timedelta(hours=1)
    assert result.publication_basis == 'original_page_metadata'
    assert 'AGGREGATOR' not in result.excerpt


@pytest.mark.parametrize('case', ['robots', 'paywall', 'private'])
def test_aihot_original_checks_keep_existing_access_protections(case):
    c = cand(publication_basis='aihot_unverified')
    content = body('<meta property="article:published_time" content="2026-10-13T00:00:00Z">')
    if case == 'private':
        c.url = 'https://127.0.0.1/a'
    if case == 'paywall':
        content += '<div class="paywall"></div>'
    with pytest.raises(ValueError):
        verifier(content, robots='User-agent: *\nDisallow: /' if case == 'robots' else '').verify(c, source())


@pytest.mark.parametrize('rest_ok', [True, False])
def test_aihot_fallback_status_reports_real_transport_and_warning(rest_ok):
    def handler(request):
        if request.method == 'POST' or not rest_ok:
            return httpx.Response(500)
        return httpx.Response(200, json={'schemaVersion': 1, 'items': [entry()]})
    http = SafeHTTP(transport=httpx.MockTransport(handler), check_dns=False, attempts=1)
    _, status = Collector(http).collect(source(), NOW)
    assert status.status == ('ok' if rest_ok else 'failed')
    assert status.transport == ('rest' if rest_ok else None)
    assert 'MCP failed: HTTP 500' in status.warning
    http.close()


def statuses():
    return [SourceStatus(source_id='aihot_'+c, name=c, kind='aihot', region='global',
        categories=[c], status='ok', checked_at=NOW) for c in CATEGORIES] + [
        SourceStatus(source_id='search_backfill', name='backfill', kind='aihot', region='global',
            categories=CATEGORIES, status='ok', checked_at=NOW)]


def test_aihot_coverage_explains_single_entry_and_never_fabricates_regions():
    coverage = check_coverage(statuses(), settings(discovery_provider='aihot', required_regions=[]))
    assert set(coverage.categories_checked) == set(CATEGORIES) and not coverage.regions_checked
    assert any('AIHOT' in s and '独立' in s for s in coverage.limitations)


@pytest.mark.parametrize('failed', ['aihot_'+c for c in CATEGORIES] + ['search_backfill'])
def test_aihot_latest_all_categories_cannot_mask_failed_domain_or_backfill(failed):
    rows = statuses()
    for s in rows:
        if s.source_id == failed:
            s.status = 'failed'
    rows.append(SourceStatus(source_id='aihot_latest', name='latest', kind='aihot', region='global',
        categories=CATEGORIES, status='ok', checked_at=NOW))
    with pytest.raises(QualityError):
        check_coverage(rows, settings(discovery_provider='aihot', required_regions=[]))


def test_aihot_coverage_rejects_successful_non_aihot_status_and_keeps_verification_gates():
    rows = statuses()
    rows[0].kind = 'web_search'
    with pytest.raises(QualityError):
        check_coverage(rows, settings(discovery_provider='aihot', required_regions=[]))
    config = settings(discovery_provider='aihot', required_regions=[])
    with pytest.raises(QualityError, match='Too few'):
        require_verified([cand()]*4, 4, config)
    with pytest.raises(QualityError, match='failure ratio'):
        require_verified([cand()]*5, 20, config)


def test_aihot_citation_uses_original_hostname_and_renders_discovery_provenance():
    c = cand(source_id='aihot_models', region='global', discovery_url=entry()['links']['aihot'],
        discovered_at=NOW, upstream_published_at=NOW-timedelta(days=1))
    ai = FakeAI([EditorialBatch(news_items=[draft(priority='P2')], rejected_candidate_ids=[]),
        review(), Synthesis(executive_summary='本期已完成公开来源检索和事实核验。', top_stories=[], trend_signals=[]),
        SynthesisReview(approved=True, unsupported_claims=[])])
    editor = Editor(ai, '', settings(), [source()])
    items, _ = editor.edit([c], EventStore({'events': {}, 'reports': {}, 'version': 1}), context())
    citation = items[0].sources[0]
    assert citation.name == 'example.com' and citation.discovery_url == c.discovery_url
    assert citation.discovered_at == NOW and citation.upstream_published_at == c.upstream_published_at
    r = report()
    r.news_items = items
    html = Renderer(ROOT/'templates').report(r)
    assert 'AIHOT发现' in html and c.discovery_url in html and '收录' in html
    old = json.loads(report().model_dump_json())
    for field in ['discovery_url', 'discovered_at', 'upstream_published_at']:
        old['news_items'][0]['sources'][0].pop(field, None)
    assert Report.model_validate(old).news_items[0].sources[0].discovery_url is None


class AihotOffline:
    def __init__(self, http):
        pass

    def collect(self, src, now):
        c = cand(id=src.id, source_id=src.id, url='https://example.com/'+src.id, region='global',
            categories=src.categories, published_at=None, time_precision='unknown', verified=False,
            publication_basis='aihot_unverified', discovery_url=entry()['links']['aihot'])
        return [c], SourceStatus(source_id=src.id, name=src.name, kind=src.kind, region=src.region,
            categories=src.categories, status='ok', transport='mcp', checked_at=now, fetched=1)

    def verify(self, c, src):
        c.published_at, c.time_precision, c.verified = NOW-timedelta(hours=1), 'exact', True
        c.publication_basis = 'original_page_metadata'
        c.excerpt = 'Public original technical release and availability evidence. '*4
        return c


def prepare_aihot(tmp_path):
    root = project(tmp_path, profile='aihot')
    return root, prepare_work(root, now=NOW, collector_factory=AihotOffline)


def test_aihot_api_run_refuses_before_constructing_paid_client(tmp_path):
    root = project(tmp_path, profile='aihot')
    calls = []
    def forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError('must not construct AI client')
    with pytest.raises(QualityError, match='Work'):
        run_daily(root, now=NOW, ai_factory=forbidden)
    assert not calls


def test_collect_command_is_sufficient_aihot_probe_without_paid_api(tmp_path, monkeypatch):
    root = project(tmp_path, profile='aihot')
    monkeypatch.setattr('ainews.cli.Collector', AihotOffline)
    assert main(['--root', str(root), 'collect', '--source', 'aihot_models']) == 0
    diagnostic = json.loads((root/'logs/source-probe.json').read_text('utf-8'))
    assert [s['source_id'] for s in diagnostic['source_status']] == ['aihot_models']
    assert diagnostic['source_status'][0]['transport'] == 'mcp'
    assert any('AIHOT' in s for s in diagnostic['limitations'])
    assert not list((root/'reports').glob('*.html'))


@pytest.mark.parametrize('stage', ['evidence', 'publish'])
def test_aihot_work_refuses_extra_discoveries(tmp_path, stage):
    root, path = prepare_aihot(tmp_path)
    extra = Discovery(source_id='aihot_models', title='Outside discovery', url='https://example.com/extra')
    if stage == 'evidence':
        discoveries = path.with_name('extra.json')
        discoveries.write_text(json.dumps([extra.model_dump()]), 'utf-8')
        with pytest.raises(QualityError, match='AIHOT.*discover'):
            work_evidence(root, path, discoveries)
    else:
        research = packet()
        research.bundle_sha256 = digest(path.read_bytes())
        research.discoveries = [extra]
        research_path = path.with_name('research.json')
        research_path.write_text(research.model_dump_json(), 'utf-8')
        with pytest.raises(QualityError, match='AIHOT.*discover'):
            publish_work(root, path, research_path, now=NOW)


def test_aihot_work_offline_prepare_evidence_publish_idempotence(tmp_path, monkeypatch):
    root, path = prepare_aihot(tmp_path)
    bundle = json.loads(path.read_text('utf-8'))
    assert bundle['search_sources'] == []
    assert any('AIHOT' in s and '原文' in s for s in bundle['limitations'])
    monkeypatch.setattr('ainews.work.Collector', AihotOffline)
    evidence_path = work_evidence(root, path)
    evidence = json.loads(evidence_path.read_text('utf-8'))
    research = packet()
    research.bundle_sha256, research.evidence_sha256 = digest(path.read_bytes()), digest(evidence_path.read_bytes())
    research.editorial = EditorialBatch(news_items=[draft(priority='P2', candidate_ids=['aihot_models'])],
        rejected_candidate_ids=[c['id'] for c in evidence['verified'] if c['id'] != 'aihot_models'])
    research.reviews = {research.editorial.news_items[0].event_key: review(supported_candidate_ids=['aihot_models'])}
    research_path = path.with_name('research.json')
    research_path.write_text(research.model_dump_json(), 'utf-8')
    assert publish_work(root, path, research_path, now=NOW, collector_factory=AihotOffline) == 'published'
    result = Report.model_validate_json((root/'data/2026-10-13.json').read_bytes())
    assert result.news_items[0].sources[0].name == 'example.com'
    assert result.coverage_report.regions_checked == []
    assert result.usage[0]['input_tokens'] is None and result.estimated_cost_usd == 0
    assert publish_work(root, path, research_path, now=NOW) == 'already_published'
    assert prepare_work(root, now=NOW) is None
    assert run_daily(root, now=NOW) == 'already_published'
