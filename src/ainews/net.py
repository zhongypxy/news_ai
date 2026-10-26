import ipaddress
import re
import socket
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import RLock
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


class RetryAfterError(RuntimeError):
    """Server cooldown, safe to display without response bodies or credentials."""

    def __init__(self, retry_after, status_code=None):
        self.retry_after = max(float(retry_after), 0)
        self.status_code = status_code
        super().__init__(f"Server cooldown; retry after {self.retry_after:.0f} seconds"
                         + (f" (HTTP {status_code})" if status_code else ""))


class SafeHTTP:
    def __init__(self, timeout=20, attempts=3, max_bytes=3_000_000, transport=None, check_dns=True,
                 sleep=time.sleep, max_retry_wait=30):
        if attempts < 1:
            raise ValueError("At least one HTTP attempt is required")
        self.attempts = attempts
        self.max_bytes = max_bytes
        self.check_dns = check_dns
        self.sleep = sleep
        self.max_retry_wait = min(max_retry_wait, 30)
        self._cooldowns = {}
        self._host_locks = {}
        self._locks_guard = RLock()
        self.client = httpx.Client(timeout=timeout, follow_redirects=False, transport=transport,
                                   headers={"User-Agent": "GlobalAIDaily/2.0 (public news metadata collector)"})

    def close(self):
        self.client.close()

    def get(self, url, allowed_domains=None, *, headers=None, sleep=None):
        return self._request_with_retry("GET", url, allowed_domains, headers=headers, sleep=sleep)

    def post_json(self, url, payload, allowed_domains=None, *, headers=None, sleep=None):
        return self._request_with_retry("POST", url, allowed_domains, payload=payload, headers=headers, sleep=sleep)

    def _request_with_retry(self, method, url, allowed_domains, *, payload=None, headers=None, sleep=None):
        current, budget = url, [0.0]
        for _ in range(5):
            validate_url(current, allowed_domains)
            host = urlsplit(current).hostname.lower()
            with self._locks_guard:
                lock = self._host_locks.setdefault(host, RLock())
            # Release the previous hop's lock before following a redirect, so
            # simultaneous A→B and B→A requests cannot deadlock. Both methods
            # share the actual destination host's lock and published cooldown.
            with lock:
                response = self._retry_locked(method, current, allowed_domains, host, payload, headers, sleep, budget)
            if not response.is_redirect:
                return response
            if method == "POST":
                raise ValueError("POST redirects are forbidden")
            current = urljoin(current, response.headers["location"])
        raise ValueError("Too many redirects")

    def _retry_locked(self, method, url, allowed_domains, host, payload, headers, sleep, budget):
        deadline, status = self._cooldowns.get(host, (0, None))
        if deadline > time.monotonic():
            raise RetryAfterError(deadline - time.monotonic(), status)
        for attempt in range(self.attempts):
            try:
                result = self._request(method, url, allowed_domains, payload=payload, headers=headers)
                self._cooldowns.pop(host, None)
                return result
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if status and status not in {408, 429, 500, 502, 503, 504}:
                    raise
                delay = min(2 ** attempt, 4)
                if status in {429, 503}:
                    retry = self._retry_after(exc.response.headers.get("retry-after"))
                    delay = retry if retry is not None else delay
                    self._cooldowns[host] = (time.monotonic() + max(delay, 1), status)
                    if attempt == self.attempts - 1 or budget[0] + delay > self.max_retry_wait:
                        raise RetryAfterError(max(delay, 1), status) from None
                if attempt == self.attempts - 1 or budget[0] + delay > self.max_retry_wait:
                    raise
                (sleep or self.sleep)(delay)
                budget[0] += delay
        raise RuntimeError("No HTTP attempts configured")

    @staticmethod
    def _retry_after(value):
        if value is None:
            return None
        try:
            return max(float(int(value.strip())), 0)
        except (ValueError, OverflowError):
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return max((parsed - datetime.now(UTC)).total_seconds(), 0)
            except (ValueError, TypeError, OverflowError):
                return None

    def _get(self, url, allowed_domains):
        return self.get(url, allowed_domains)

    def _request(self, method, url, allowed_domains, *, payload=None, headers=None):
        validate_url(url, allowed_domains)
        if self.check_dns:
            addresses = socket.getaddrinfo(urlsplit(url).hostname, None, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("DNS resolved to non-public address")
        kwargs = {"headers": headers}
        if method == "POST":
            kwargs["json"] = payload
        with self.client.stream(method, url, **kwargs) as response:
            if response.is_redirect:
                return httpx.Response(response.status_code, headers=response.headers, request=response.request)
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > self.max_bytes:
                    raise ValueError("Response exceeds size limit")
                chunks.append(chunk)
            # iter_bytes already decodes gzip/deflate; do not decode the buffered response twice.
            result_headers = {k: v for k, v in response.headers.items()
                              if k.lower() not in {"content-encoding", "content-length"}}
            return httpx.Response(response.status_code, headers=result_headers,
                                  content=b"".join(chunks), request=response.request)
