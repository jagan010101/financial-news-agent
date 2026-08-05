"""
finrag.ingest.http — shared HTTP client: real browser headers, per-host rate
limiting, and retry/backoff. Every adapter fetches through here so politeness
and anti-ban behavior are uniform and centrally tunable.

Why this matters (proven empirically): financial sites 403 the default
feedparser/python user-agent. A browser UA + Accept headers is mandatory even
for "open" RSS. NSE additionally needs cookie seeding (handled in its adapter).
"""
from __future__ import annotations

import threading
import time

import httpx
from tenacity import (
    retry, retry_if_exception_type, stop_after_attempt, wait_exponential,
)

from finrag.config import settings

_BROWSER_HEADERS = {
    "User-Agent": settings.user_agent,
    "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# Per-host last-request timestamps for rate limiting.
_last_hit: dict[str, float] = {}
_lock = threading.Lock()


def _throttle(host: str, min_interval: float) -> None:
    with _lock:
        now = time.monotonic()
        prev = _last_hit.get(host, 0.0)
        wait = min_interval - (now - prev)
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


class HttpClient:
    """Thin wrapper over httpx.Client with throttle + retry."""

    def __init__(self, min_interval: float = 1.0, headers: dict | None = None) -> None:
        self.min_interval = min_interval
        self._client = httpx.Client(
            headers={**_BROWSER_HEADERS, **(headers or {})},
            timeout=settings.request_timeout,
            follow_redirects=True,
        )

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def get(self, url: str) -> httpx.Response:
        host = httpx.URL(url).host or ""
        _throttle(host, self.min_interval)
        r = self._client.get(url)
        # Retry on transient server errors / rate limits, not on 4xx client errors.
        if r.status_code in (429, 500, 502, 503, 504):
            r.raise_for_status()
        return r

    def close(self) -> None:
        self._client.close()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()
