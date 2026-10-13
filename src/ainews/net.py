import ipaddress
import re
import socket
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

TRACKING = {"fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid"}


def validate_url(url, allowed_domains=None):
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Unsafe URL scheme or credentials")
    host = parts.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ValueError("Private hostname is forbidden")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Non-public address is forbidden")
    except ValueError as exc:
        if "forbidden" in str(exc):
            raise
    if parts.port and parts.port not in {80, 443}:
        raise ValueError("Nonstandard ports are forbidden")
    if allowed_domains and not any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed_domains):
        raise ValueError("URL outside source allowed domains")
    # Reject credential-bearing queries rather than logging/storing them.
    for key, _ in parse_qsl(parts.query):
        if re.search(r"token|secret|password|api.?key|signature", key, re.I):
            raise ValueError("Sensitive URL parameters are forbidden")
    return url


def normalize_url(url):
    validate_url(url)
    parts = urlsplit(url)
    host = parts.hostname.encode("idna").decode().lower()
    port = f":{parts.port}" if parts.port and parts.port != (443 if parts.scheme == "https" else 80) else ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host + port, path, urlencode(sorted(query)), ""))


class SafeHTTP:
    def __init__(self, timeout=20, attempts=3, max_bytes=3_000_000, transport=None, check_dns=True):
        self.attempts = attempts
        self.max_bytes = max_bytes
        self.check_dns = check_dns
        self.client = httpx.Client(timeout=timeout, follow_redirects=False, transport=transport,
                                   headers={"User-Agent": "GlobalAIDaily/2.0 (public news metadata collector)"})

    def close(self):
        self.client.close()

    def get(self, url, allowed_domains=None):
        for attempt in range(self.attempts):
            try:
                return self._get(url, allowed_domains)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {408, 429, 500, 502, 503, 504}:
                    raise
                if attempt == self.attempts - 1:
                    raise
                time.sleep(min(2 ** attempt, 4))
        raise RuntimeError("No HTTP attempts configured")

    def _get(self, url, allowed_domains):
        current = url
        for _ in range(5):
            validate_url(current, allowed_domains)
            if self.check_dns:
                addresses = socket.getaddrinfo(urlsplit(current).hostname, None, type=socket.SOCK_STREAM)
                if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                    raise ValueError("DNS resolved to non-public address")
            with self.client.stream("GET", current) as response:
                if response.is_redirect:
                    current = urljoin(current, response.headers["location"])
                    continue
                response.raise_for_status()
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ValueError("Response exceeds size limit")
                    chunks.append(chunk)
                return httpx.Response(response.status_code, headers=response.headers,
                                      content=b"".join(chunks), request=response.request)
        raise ValueError("Too many redirects")
