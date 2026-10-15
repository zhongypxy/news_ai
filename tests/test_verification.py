from datetime import timedelta

import httpx
import pytest
from test_analysis import NOW, cand

from ainews.collectors import Collector
from ainews.models import Source
from ainews.net import SafeHTTP


def verifier(body, robots="User-agent: *\nAllow: /", status=200):
    def handler(request):
        return httpx.Response(200 if request.url.path=="/robots.txt" else status,
                              text=robots if request.url.path=="/robots.txt" else body,
                              headers={"content-type": "text/plain" if request.url.path=="/robots.txt" else "text/html"},
                              request=request)
    return Collector(SafeHTTP(transport=httpx.MockTransport(handler), check_dns=False, attempts=1))


def source(kind="rss"):
    return Source(id="s", kind=kind, url="https://example.com/rss", allowed_domains=["example.com"],
                  timezone="UTC")


def body(meta):
    return '<html><head>'+meta+'</head><body><article><h1>Model release</h1><p>' + "Official technical details of release, model capabilities and public availability. "*5 + '</p></article></body></html>'


def test_original_metadata_can_resolve_missing_date():
    item = cand(published_at=None, time_precision="unknown")
    collector = verifier(body('<meta property="article:published_time" content="2026-10-13T00:00:00Z">'))
    result = collector.verify(item, source())
    assert result.verified and result.published_at == NOW-timedelta(hours=1)
    assert result.publication_basis == "original_page_metadata"


def test_search_claimed_date_is_not_treated_as_original_evidence():
    item = cand(publication_basis="search_unverified")
    with pytest.raises(ValueError, match="original source metadata"):
        verifier(body("")).verify(item, source("web_search"))


def test_github_release_uses_release_time_instead_of_commit_verification_time():
    item = cand(url="https://github.com/owner/project/releases/tag/v1.0", published_at=None,
                time_precision="unknown", publication_basis="search_unverified")
    src = source("web_search").model_copy(update={"allowed_domains": ["github.com"]})
    html = body("").replace('<article>', '<div>Verified <relative-time datetime="2026-10-12T01:00:00Z"></relative-time></div>'
                           '<div>released this <relative-time datetime="2026-10-13T00:00:00Z"></relative-time></div><article>')
    result = verifier(html).verify(item, src)
    assert result.published_at == NOW-timedelta(hours=1)
    assert result.publication_basis == "original_page_metadata"


@pytest.mark.parametrize("url", ["https://example.com/releases/tag/v1", "https://github.com/owner/project/commit/123"])
def test_release_time_markup_is_not_accepted_on_unrelated_pages(url):
    src = source("web_search").model_copy(update={"allowed_domains": []})
    html = body("").replace('<article>', '<div>released this <relative-time datetime="2026-10-13T00:00:00Z"></relative-time></div><article>')
    with pytest.raises(ValueError, match="original source metadata"):
        verifier(html).verify(cand(url=url, publication_basis="search_unverified"), src)


def test_conflicting_original_time_blocks_candidate():
    html = body('<meta property="article:published_time" content="2026-09-01T00:00:00Z">')
    with pytest.raises(ValueError, match="Conflicting"):
        verifier(html).verify(cand(), source())


def test_robots_rejection_is_not_bypassed():
    with pytest.raises(ValueError, match="disallows"):
        verifier(body(""), robots="User-agent: *\nDisallow: /").verify(cand(), source("web_search"))


def test_jsonld_metadata_and_paywall_rejection():
    html = body('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-10-13T00:00:00Z"}</script>')
    assert verifier(html).verify(cand(publication_basis="search_unverified"), source("web_search")).verified
    paywall = body('<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-10-13T00:00:00Z","isAccessibleForFree":false}</script>')
    with pytest.raises(ValueError, match="Paywalled"):
        verifier(paywall).verify(cand(), source())
def test_missing_robots_file_404_allows_public_page_but_403_fails():
    import httpx
    import pytest

    from ainews.collectors import Collector

    class MissingRobots:
        def get(self, url, domains=None):
            response = httpx.Response(self.code, request=httpx.Request('GET', url))
            response.raise_for_status()
            return response
    http = MissingRobots()
    http.code = 404
    Collector(http).check_robots('https://example.com/article')
    http.code = 403
    with pytest.raises(httpx.HTTPStatusError):
        Collector(http).check_robots('https://example.com/article')
