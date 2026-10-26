import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
from bs4 import BeautifulSoup

from .aihot import AihotClient, validate_collection_source
from .models import Candidate, SourceStatus
from .net import normalize_url, validate_url
from .quality import FatalAPIError
from .timeutil import parse_time


def plain(text, limit=1800):
    soup = BeautifulSoup(str(text or ""), "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


def nested(value, path, default=None):
    if not path:
        return value
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def aihot_original(url):
    validate_url(url)
    host = urlsplit(url).hostname.lower()
    if host == "aihot.news" or host.endswith(".aihot.news"):
        raise ValueError("AIHOT page cannot be an original source")
    return normalize_url(url)


def candidate(source, title, url, published, excerpt, now, basis="source_metadata"):
    validate_url(url, source.allowed_domains)
    url = normalize_url(url)
    stamp, precision = parse_time(published, source.timezone)
    identity = hashlib.sha256(f"{source.id}|{url}".encode()).hexdigest()[:20]
    return Candidate(id=identity, source_id=source.id, title=plain(title, 500), url=url,
                     excerpt=plain(excerpt), published_at=stamp, time_precision=precision,
                     collected_at=now, region=source.region, categories=source.categories, publication_basis=basis)


class Collector:
    def __init__(self, http, search=None):
        self.http = http
        self.search = search
        self.robots = {}
        self.aihot_client = AihotClient(http)

    def collect(self, source, now):
        status = SourceStatus(source_id=source.id, name=source.name or source.id, kind=source.kind,
                              region=source.region, categories=source.categories, status="ok", checked_at=now)
        if not source.enabled:
            status.status = "disabled"
            return [], status
        try:
            if source.kind == "aihot":
                items = self.aihot(source, now, status)
            elif source.kind == "web_search":
                if self.search is None:
                    status.status = "skipped"
                    status.error = "Web Search requires authorized OPENAI_API_KEY"
                    return [], status
                items = self.search(source, now)
            else:
                if source.kind == "page" and source.respect_robots:
                    self.check_robots(source.url, source.allowed_domains)
                response = self.http.get(source.url, source.allowed_domains)
                if source.kind == "rss":
                    items = self.rss(source, response.content, now)
                elif source.kind == "api":
                    items = self.api(source, response.json(), now)
                else:
                    items = self.page(source, response.text, now)
            status.fetched = len(items)
            return items, status
        except FatalAPIError:
            raise
        except Exception as exc:
            # Exception messages can contain signed URLs, headers or credentials; never persist them.
            status.status = "failed"
            code = getattr(getattr(exc, "response", None), "status_code", None)
            status.error = type(exc).__name__ + (f" HTTP {code}" if code else "")
            return [], status

    def aihot(self, source, now, status):
        validate_collection_source(source)
        queries = source.queries if source.mcp_tool == "aihot_search" else [None]
        items, seen, transports, warnings = [], set(), set(), []
        for query in queries:
            arguments = source.mcp_arguments | ({"q": query} if query is not None else {})
            try:
                data = self.aihot_client.call(source.mcp_tool, arguments)
            finally:
                if self.aihot_client.last_transport:
                    transports.add(self.aihot_client.last_transport)
                warning = self.aihot_client.last_warning
                if warning and warning not in warnings:
                    warnings.append(warning)
                status.transport = "+".join(sorted(transports)) or None
                status.warning = "; ".join(warnings) or None
            entries = data.get("items")
            if not isinstance(entries, list):
                raise ValueError("AIHOT structured items must be an array")
            valid = 0
            for raw in entries:
                try:
                    item = self.aihot_candidate(source, raw, now)
                except (ValueError, TypeError, AttributeError):
                    continue
                valid += 1
                if item.url not in seen:
                    seen.add(item.url)
                    items.append(item)
            if entries and not valid:
                raise ValueError("All AIHOT original links rejected")
        return items[:min(source.max_items, source.mcp_arguments["limit"])]

    @staticmethod
    def aihot_candidate(source, raw, now):
        links = raw.get("links", {})
        original, discovery = links.get("original"), links.get("aihot")
        if not isinstance(original, str) or not isinstance(discovery, str):
            raise ValueError("AIHOT original and discovery URLs are required")
        url = aihot_original(original)
        validate_url(discovery)
        parsed = urlsplit(discovery)
        if (parsed.scheme != "https" or parsed.netloc != "aihot.news" or parsed.query or parsed.fragment
                or not re.fullmatch(r"/items/[A-Za-z0-9_-]+", parsed.path)):
            raise ValueError("Invalid AIHOT discovery URL")
        if raw.get("id") and parsed.path != "/items/" + str(raw["id"]):
            raise ValueError("AIHOT discovery URL does not match item ID")
        identity = hashlib.sha256(f"{source.id}|{url}".encode()).hexdigest()[:20]
        return Candidate(id=identity, source_id=source.id, title=plain(raw.get("title"), 300),
            url=url, excerpt=plain(raw.get("summary"), 500), published_at=None, time_precision="unknown",
            collected_at=now, region="global", categories=source.categories, publication_basis="aihot_unverified",
            discovery_url=discovery, discovered_at=parse_time(raw.get("discoveredAt"))[0],
            upstream_published_at=parse_time(raw.get("publishedAt"))[0])

    def check_robots(self, url, domains=None):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            robots = RobotFileParser()
            try:
                response = self.http.get(origin + "/robots.txt", domains)
                robots.parse(response.text.splitlines())
            except httpx.HTTPStatusError as exc:
                # RFC 9309 §2.3.1.3: an absent robots file is not a crawl restriction.
                # Keep authentication/denial and server failures conservative.
                if exc.response.status_code not in {404, 410}:
                    raise
                robots.parse([])
            self.robots[origin] = robots
        if not self.robots[origin].can_fetch("GlobalAIDaily", url):
            raise ValueError("robots.txt disallows collection")

    def rss(self, source, body, now):
        feed = feedparser.parse(body)
        if not feed.get("version"):
            raise ValueError("Response is not an RSS/Atom feed")
        items = []
        for item in feed.entries[:source.max_items]:
            try:
                items.append(candidate(source, item.get("title", ""), item.get("link", ""),
                                       item.get("published"), item.get("summary", ""), now))
            except (ValueError, TypeError):
                continue
        if feed.entries and not items:
            raise ValueError("All feed entries rejected")
        return items

    def api(self, source, body, now):
        entries = nested(body, source.items_path)
        if not isinstance(entries, list):
            raise ValueError("API items must be a JSON array")
        fields = {"title": "title", "url": "url", "published_at": "published_at", "excerpt": "summary"} | source.fields
        items = []
        for item in entries[:source.max_items]:
            try:
                items.append(candidate(source, nested(item, fields["title"], ""), nested(item, fields["url"], ""),
                                       nested(item, fields["published_at"]), nested(item, fields["excerpt"], ""), now))
            except (ValueError, TypeError):
                continue
        if entries and not items:
            raise ValueError("All API entries rejected")
        return items

    def page(self, source, body, now):
        soup = BeautifulSoup(body, "html.parser")
        nodes = soup.select(source.item_selector)
        if not nodes:
            raise ValueError("Page selectors no longer match")
        items = []
        for node in nodes[:source.max_items]:
            title = node.select_one(source.title_selector)
            link = node if source.link_selector == ":scope" else node.select_one(source.link_selector)
            stamp = node.select_one(source.date_selector)
            excerpt = node.select_one(source.excerpt_selector)
            if not title or not link:
                continue
            try:
                items.append(candidate(source, title.get_text(), urljoin(source.url, link.get("href", "")),
                                       stamp.get("datetime", stamp.get_text()) if stamp else None,
                                       excerpt.get_text() if excerpt else title.get_text(), now))
            except (ValueError, TypeError):
                continue
        if not items:
            raise ValueError("No usable page entries")
        return items

    def verify(self, item, source):
        """Read a public source; prefer explicit publication metadata, never HTTP Date/updated time."""
        domains = None if source.kind == "aihot" else source.allowed_domains or None
        validate_url(item.url, domains)
        if source.kind == "aihot":
            aihot_original(item.url)
            # AIHOT timestamps are provenance only. Every verification must find
            # an original publication dateline, including a second publication pass.
            item.published_at, item.time_precision = None, "unknown"
            item.publication_basis, item.verified = "aihot_unverified", False
        if source.respect_robots:
            self.check_robots(item.url, domains)
        response = self.http.get(item.url, domains)
        parsed = urlsplit(str(response.url))
        if source.kind == "aihot":
            aihot_original(str(response.url))
        rule = source.publication_rules.get(parsed.hostname or "")
        publication_timezone = rule.timezone if rule else source.timezone
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            raise ValueError("Source is not readable HTML")
        soup = BeautifulSoup(response.text, "html.parser")
        if soup.select('[isAccessibleForFree="false"], .paywall, #paywall'):
            raise ValueError("Paywalled source")
        for node in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            if node.name != "script" or node.get("type") != "application/ld+json":
                node.decompose()
        stamp = None
        basis = "original_page_metadata"
        for name in ["article:published_time", "datePublished", "date", "pubdate"]:
            meta = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if meta and meta.get("content"):
                stamp = meta["content"]
                break
        for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                blocks = json.loads(node.string or "{}")
            except (ValueError, TypeError):
                continue
            blocks = blocks if isinstance(blocks, list) else [blocks]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                for part in [block] + block.get("@graph", []):
                    if not isinstance(part, dict):
                        continue
                    if part.get("isAccessibleForFree") in {False, "false"}:
                        raise ValueError("Paywalled source")
                    if not stamp and part.get("datePublished"):
                        stamp = part["datePublished"]
        # GitHub release pages expose publication in a custom element; nearby
        # signed-commit timestamps describe verification, not publication.
        if not stamp and rule:
            parts = []
            for selector in rule.selectors:
                nodes = soup.select(selector)
                if len(nodes) != 1:
                    raise ValueError("Configured publication dateline missing or ambiguous")
                value = nodes[0].get(rule.attribute) if rule.attribute else nodes[0].get_text(" ", strip=True)
                if not value or len(value) > 120:
                    raise ValueError("Configured publication dateline is not a date field")
                parts.append(value)
            stamp = " ".join(parts)
            basis = "original_page_configured_dateline"
        if not stamp and parsed.hostname == "github.com" and re.fullmatch(r"/[^/]+/[^/]+/releases/tag/[^/]+", parsed.path):
            release_times = [node.get("datetime") for node in soup.select("relative-time[datetime]")
                             if "released this" in node.parent.get_text(" ", strip=True)]
            if len(release_times) == 1:
                stamp = release_times[0]
        if stamp:
            published, precision = parse_time(stamp, publication_timezone)
            if published:
                if item.published_at and item.time_precision == "exact" and abs((published - item.published_at).total_seconds()) > 86400:
                    raise ValueError("Conflicting source publication times")
                item.published_at, item.time_precision = published, precision
                item.publication_basis = basis
        if source.kind == "web_search" and item.publication_basis == "search_unverified":
            raise ValueError("Search publication time lacks original source metadata")
        if source.kind == "aihot" and item.publication_basis == "aihot_unverified":
            raise ValueError("AIHOT publication time lacks original source metadata")
        if item.published_at is None:
            raise ValueError("Publication time unavailable")
        for node in soup("script"):
            node.decompose()
        article = soup.select_one(".markdown-body") or soup.find("article") or soup.find("main") or soup.body or soup
        links = []
        references = [node['href'] for node in article.select('a[href]')]
        # Some publishers print project addresses as visible text rather than anchors.
        # Scripts were removed above; use the same URL validation and cap as hrefs.
        references.extend(re.findall(r"https?://[^\s<>\"'，。；：！？（）【】《》)]+", article.get_text(" ", strip=True)))
        for reference in references:
            url = urljoin(str(response.url), reference.rstrip(".,;:!?]}"))
            try:
                validate_url(url)
                url = normalize_url(url)
            except ValueError:
                continue
            if url not in links:
                links.append(url)
        item.linked_urls = links[:30]
        text = plain(article.get_text(" ", strip=True), 1800)
        if len(text) < 60:
            raise ValueError("Insufficient original evidence")
        item.excerpt = text
        item.verified = True
        item.verification_note = "public_original_read; factual_support_checked_by_editor"
        return item
