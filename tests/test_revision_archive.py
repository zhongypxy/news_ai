import json
from datetime import timedelta

import pytest
from test_pipeline import project
from test_publish import NOW, ROOT, publisher, report, state

from ainews.publish import digest
from ainews.quality import QualityError
from ainews.render import Renderer
from ainews.timeutil import research_window
from ainews.work import prepare_work


def revision_archive(tmp_path):
    pub = publisher(tmp_path)
    pub.publish(report(), state())
    record = pub.load_state()
    revised = report('修订版项目报道')
    revised.window_end += timedelta(hours=2)
    revised.executive_summary = '修订后摘要'
    paths = {'html': 'reports/2026-10-13-r2.html', 'json': 'data/2026-10-13-r2.json'}
    (tmp_path / paths['html']).write_text(pub.renderer.report(revised), encoding='utf-8')
    (tmp_path / paths['json']).write_text(revised.model_dump_json(), encoding='utf-8')
    record['reports']['2026-10-13']['latest_revision'] = {
        **paths, 'count': len(revised.all_items), 'summary': revised.executive_summary,
        'window_end': revised.window_end.isoformat(),
        'sha256': {path: digest((tmp_path / path).read_bytes()) for path in paths.values()},
    }
    (tmp_path / 'state/events.json').write_text(json.dumps(record), encoding='utf-8')
    return pub, record


def test_history_links_latest_revision_and_keeps_original(tmp_path):
    _, record = revision_archive(tmp_path)
    html = Renderer(ROOT / 'templates').index(record['reports'])
    assert 'href="reports/2026-10-13-r2.html"' in html
    assert 'href="reports/2026-10-13.html"' in html
    assert 'href="data/2026-10-13-r2.json"' in html
    assert '修订后摘要' in html


def test_next_window_uses_verified_revision_cutoff(tmp_path):
    _, record = revision_archive(tmp_path)
    now = NOW + timedelta(days=1)
    start, end = research_window(now, record, now.date())
    assert start == NOW + timedelta(hours=2)
    assert end == now


def test_completeness_checks_revision_without_changing_original(tmp_path):
    pub, record = revision_archive(tmp_path)
    original = (tmp_path / 'reports/2026-10-13.html').read_bytes()
    assert pub.completed('2026-10-13')
    path = tmp_path / record['reports']['2026-10-13']['latest_revision']['json']
    path.write_text('corrupted', encoding='utf-8')
    with pytest.raises(QualityError, match='integrity'):
        pub.completed('2026-10-13')
    assert (tmp_path / 'reports/2026-10-13.html').read_bytes() == original


@pytest.mark.parametrize('case', ['null', 'empty', 'missing_summary'])
def test_completeness_rejects_malformed_revision_metadata(tmp_path, case):
    pub, record = revision_archive(tmp_path)
    entry = record['reports']['2026-10-13']
    if case == 'null':
        entry['latest_revision'] = None
    elif case == 'empty':
        entry['latest_revision'] = {}
    else:
        del entry['latest_revision']['summary']
    pub.state_path.write_text(json.dumps(record), encoding='utf-8')

    with pytest.raises(QualityError, match='revision|integrity'):
        pub.completed('2026-10-13')


@pytest.mark.parametrize('case', ['html_is_json', 'json_is_html', 'cross_day', 'original'])
def test_completeness_rejects_invalid_revision_file_roles(tmp_path, case):
    pub, record = revision_archive(tmp_path)
    entry = record['reports']['2026-10-13']
    revision = entry['latest_revision']
    if case == 'html_is_json':
        revision['html'] = revision['json']
    elif case == 'json_is_html':
        revision['json'] = revision['html']
    elif case == 'cross_day':
        # Both files and hashes remain valid; only their claimed archive day differs.
        for role in ('html', 'json'):
            old_path = revision[role]
            new_path = old_path.replace('2026-10-13', '2026-10-12')
            (tmp_path / new_path).write_bytes((tmp_path / old_path).read_bytes())
            revision[role] = new_path
        revision['sha256'] = {
            revision[role]: digest((tmp_path / revision[role]).read_bytes())
            for role in ('html', 'json')
        }
    else:
        revision['html'] = 'reports/2026-10-13.html'
        revision['json'] = 'data/2026-10-13.json'
        revision['sha256'] = dict(entry['sha256'])
        revision['window_end'] = entry['window_end']
        revision['count'] = entry['count']
        revision['summary'] = entry['summary']
    pub.state_path.write_text(json.dumps(record), encoding='utf-8')

    with pytest.raises(QualityError, match='revision|integrity'):
        pub.completed('2026-10-13')


@pytest.mark.parametrize('case', ['damaged_json', 'unverified_cutoff'])
def test_prepare_rejects_damaged_historical_revision_before_collecting(tmp_path, case):
    root = project(tmp_path)
    pub, record = revision_archive(root)
    now = NOW + timedelta(days=1)
    revision = record['reports']['2026-10-13']['latest_revision']
    if case == 'damaged_json':
        (root / revision['json']).write_text('corrupted', encoding='utf-8')
    else:
        # A metadata-only change must not silently skip almost a whole day of news.
        revision['window_end'] = (now - timedelta(minutes=1)).isoformat()
        pub.state_path.write_text(json.dumps(record), encoding='utf-8')

    def forbidden_collector(http):
        pytest.fail('Historical revision integrity must be checked before collection')

    with pytest.raises(QualityError, match='revision|integrity'):
        prepare_work(root, now=now, collector_factory=forbidden_collector)
