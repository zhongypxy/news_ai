from datetime import UTC, date, datetime

import httpx
import pytest
from pydantic import ValidationError

from ainews.collectors import Collector
from ainews.models import Candidate, Source
from ainews.net import SafeHTTP, normalize_url, validate_url
from ainews.timeutil import parse_time, report_window


def test_window_is_fixed_and_exclusive():
    start, end = report_window(date(2026, 10, 13), "Asia/Singapore", 9)
    assert start.isoformat() == "2026-10-12T01:00:00+00:00"
    assert end.isoformat() == "2026-10-13T01:00:00+00:00"
    assert (end - start).total_seconds() == 86400


def test_timezone_and_date_only_precision():
    assert parse_time("2026-10-13T09:00:00+08:00")[0] == datetime(2026, 10, 13, 1, tzinfo=UTC)
    assert parse_time("2026-10-13", "Asia/Shanghai")[1] == "date"
    assert parse_time(None) == (None, "unknown")
    assert parse_time("2026-10-13T09:00:00")[0] is None


def test_url_normalization_and_security():
    assert normalize_url("https://EXAMPLE.com/a/?utm_source=x&id=2#foo") == "https://example.com/a?id=2"
    for url in ["file:///etc/passwd", "https://u:p@example.com", "http://127.0.0.1/x", "https://localhost"]:
        with pytest.raises(ValueError):
            validate_url(url)
    with pytest.raises(ValueError):
        validate_url("https://evil.example/news", ["example.com"])


def test_strict_candidate_schema():
    with pytest.raises(ValidationError):
        Candidate(id="x", source_id="s", title="x", url="https://example.com", excerpt="x",
                  published_at=None, time_precision="unknown", collected_at=datetime.now(), region="global",
                  extra_field="bad")


def collector(body, content_type="application/xml", status=200):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body,
                                       headers={"Content-Type": content_type}, request=request))
    client = SafeHTTP(transport=transport, check_dns=False, attempts=1)
    return Collector(client)


def test_rss_collects_short_metadata():
    body = '<rss version="2.0"><channel><title>Test</title><item><title>Model</title><link>https://example.com/news</link><pubDate>Mon, 12 Oct 2026 12:00:00 GMT</pubDate><description>Release</description></item></channel></rss>'
    source = Source(id="test", kind="rss", url="https://example.com/feed", allowed_domains=["example.com"])
    items, status = collector(body).collect(source, datetime.now(UTC))
    assert status.status == "ok"
    assert len(items) == 1
    assert items[0].published_at == datetime(2026, 10, 12, 12, tzinfo=UTC)


def test_api_and_html_publish_page():
    source = Source(id="api", kind="api", url="https://example.com/api", allowed_domains=["example.com"],
                    fields={"title": "name", "url": "html_url", "published_at": "published_at", "excerpt": "body"})
    items, status = collector('[{"name":"Release","html_url":"https://example.com/r","published_at":"2026-10-12T12:00:00Z","body":"Short"}]', "application/json").collect(source, datetime.now(UTC))
    assert status.status == "ok" and len(items) == 1
    source = Source(id="page", kind="page", url="https://example.com/news", allowed_domains=["example.com"],
                    item_selector="article", title_selector="h2", link_selector="a", date_selector="time",
                    respect_robots=False)
    items, status = collector('<article><h2>Launch</h2><a href="/launch">read</a><time datetime="2026-10-12T12:00:00Z">now</time></article>', "text/html").collect(source, datetime.now(UTC))
    assert status.status == "ok" and items[0].url == "https://example.com/launch"


def test_failed_collection_never_becomes_empty_success():
    source = Source(id="rss", kind="rss", url="https://example.com/rss", allowed_domains=["example.com"])
    items, status = collector("Blocked", status=403).collect(source, datetime.now(UTC))
    assert not items and status.status == "failed"
    items, status = collector("<html>login</html>").collect(source, datetime.now(UTC))
    assert not items and status.status == "failed"


def test_redirect_is_checked_before_following():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"}, request=request)
    client = SafeHTTP(transport=httpx.MockTransport(handler), check_dns=False, attempts=1)
    with pytest.raises(ValueError):
        client.get("https://example.com/")
    assert len(calls) == 1
