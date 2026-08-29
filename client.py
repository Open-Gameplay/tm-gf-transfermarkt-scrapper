"""HTTP layer: rate-limited, resume-cached, circuit-broken client.

One client instance is shared by all fetch modules and workers. Requests are
rate-limited globally per bucket:

- bucket="html"  — local transfermarkt-api endpoints + direct TM HTML
                   (each API call == one live TM request, so the html bucket
                   is the real guard against TM bans);
- bucket="cdn"   — image downloads from img.a.transfermarkt.technology, ...

Transport is curl_cffi (impersonates Chrome TLS fingerprint) with a fallback
to plain requests. On 429/5xx the client backs off (respecting Retry-After)
and opens a circuit breaker after consecutive failures instead of hammering.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import requests

from cache import ResumeCache
from config import (
    API_BASE,
    CACHE_ENABLED,
    CACHE_PATH,
    CACHE_TTL,
    CDN_BURST,
    CDN_RPS,
    CIRCUIT_FAILS,
    CIRCUIT_PAUSE,
    CONCURRENCY,
    DEFAULT_HEADERS,
    HTML_BURST,
    HTML_RPS,
    HTTP_MAX_RETRIES,
    HTTP_TIMEOUT,
    USER_AGENTS,
)

logger = logging.getLogger("scraper.client")

try:
    from curl_cffi import requests as cffi_requests
    BACKEND = "curl_cffi"
except ImportError:  # pragma: no cover - fallback transport
    cffi_requests = requests
    BACKEND = "requests"

RETRY_STATUSES = {429, 500, 502, 503, 504, 520, 522, 524}

# Signatures of a Cloudflare/anti-bot block page (checked on direct TM HTML).
BLOCK_SIGNATURES = ("cf-chl-", "attention required", "are you a human",
                    "cf-browser-verification", "challenge-platform")

# Hosts treated as CDN (fast, not throttled like www.transfermarkt.com).
CDN_HOSTS = ("img.a.transfermarkt.technology", "tmssl.akamaized.net")


class TokenBucket:
    """Thread-safe token bucket: `rps` tokens/sec, capacity = burst."""

    def __init__(self, rps: float, burst: int):
        self.rps = max(rps, 0.01)
        self.capacity = max(burst, 1)
        self.tokens = float(self.capacity)
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rps)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rps
            time.sleep(wait)


class CircuitBreaker:
    """After `threshold` consecutive failures, pause all requests for `pause`s."""

    def __init__(self, threshold: int, pause: float):
        self.threshold = max(threshold, 1)
        self.pause = pause
        self.fails = 0
        self.opened_until = 0.0
        self.lock = threading.Lock()

    def gate(self) -> None:
        with self.lock:
            wait = self.opened_until - time.monotonic()
            if wait <= 0:
                self.opened_until = 0.0
                return
        logger.warning("Circuit breaker open, sleeping %.1fs", wait)
        time.sleep(wait)

    def on_success(self) -> None:
        with self.lock:
            self.fails = 0
            self.opened_until = 0.0

    def on_failure(self) -> None:
        with self.lock:
            self.fails += 1
            if self.fails >= self.threshold:
                self.opened_until = time.monotonic() + self.pause
                logger.error("Circuit breaker OPEN for %.1fs (%d consecutive failures)",
                             self.pause, self.fails)


class _Response:
    """Thin requests.Response-like object for cache hits."""

    def __init__(self, status: int, body: bytes, url: str):
        self.status_code = status
        self.content = body
        self.url = url
        self.headers: dict[str, str] = {}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise requests.HTTPError(f"{self.status_code} for {self.url}")

    def close(self) -> None:
        pass


class Client:
    """Thread-safe HTTP client with per-bucket rate limiting and resume cache."""

    def __init__(
        self,
        api_base: str = API_BASE,
        *,
        html_rps: float = HTML_RPS,
        html_burst: int = HTML_BURST,
        cdn_rps: float = CDN_RPS,
        cdn_burst: int = CDN_BURST,
        timeout: float = HTTP_TIMEOUT,
        max_retries: int = HTTP_MAX_RETRIES,
        concurrency: int = CONCURRENCY,
        circuit_fails: int = CIRCUIT_FAILS,
        circuit_pause: float = CIRCUIT_PAUSE,
        cache: ResumeCache | None = None,
    ):
        self.api_base = api_base
        self.timeout = timeout
        self.max_retries = max_retries
        self._buckets = {
            "html": TokenBucket(html_rps, html_burst),
            "cdn": TokenBucket(cdn_rps, cdn_burst),
        }
        self._circuit = CircuitBreaker(circuit_fails, circuit_pause)
        self._cache = cache if cache is not None else (
            ResumeCache(CACHE_PATH, CACHE_TTL) if CACHE_ENABLED else None
        )
        self._sem = threading.Semaphore(concurrency)
        self._local = threading.local()
        logger.info(
            "client: backend=%s html=%.2frps cdn=%.2frps concurrency=%d cache=%s",
            BACKEND, html_rps, cdn_rps, concurrency, "on" if self._cache else "off",
        )

    # --- sessions ------------------------------------------------------------
    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            if BACKEND == "curl_cffi":
                s = cffi_requests.Session(impersonate="chrome131")
            else:
                s = cffi_requests.Session()
            s.headers.update(DEFAULT_HEADERS)
            self._local.session = s
        return s

    @staticmethod
    def _ua() -> str:
        return random.choice(USER_AGENTS)

    # --- core ----------------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        bucket: str = "html",
        use_cache: bool = True,
        timeout: float | None = None,
        max_retries: int | None = None,
        **kwargs,
    ) -> _Response:
        """Perform one request with cache, rate limit, circuit and retries."""
        method = method.upper()
        timeout = timeout or self.timeout
        max_retries = max_retries or self.max_retries

        if use_cache and method == "GET" and self._cache:
            hit = self._cache.get(url)
            if hit:
                return _Response(hit[0], hit[1], url)

        if bucket not in self._buckets:
            bucket = "html"

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            self._circuit.gate()
            self._buckets[bucket].acquire()

            with self._sem:
                headers = dict(kwargs.pop("headers", {}) or {})
                headers.setdefault("User-Agent", self._ua())
                try:
                    resp = self._session().request(
                        method, url, timeout=timeout, headers=headers, **kwargs
                    )
                except (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError) as exc:
                    last_exc = exc
                    self._circuit.on_failure()
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(_backoff(attempt))
                    continue
                except Exception as exc:  # curl_cffi has its own exceptions
                    last_exc = exc
                    self._circuit.on_failure()
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(_backoff(attempt))
                    continue

            status = resp.status_code

            if status == 429 or status in RETRY_STATUSES:
                self._circuit.on_failure()
                if attempt == max_retries - 1:
                    self._cache_result(url, status, resp, use_cache, method)
                    return _from_resp(resp)
                wait = _backoff(attempt)
                wait = max(wait, _retry_after(resp.headers.get("Retry-After"), wait))
                logger.warning(
                    "HTTP %d on %s -> sleep %.1fs (attempt %d/%d)",
                    status, url, wait, attempt + 1, max_retries,
                )
                _close(resp)
                time.sleep(wait)
                continue

            if _is_blocked_direct(resp) and _is_direct_tm(url):
                logger.warning("Blocked page signature on %s", url)
                self._circuit.on_failure()
                if attempt == max_retries - 1:
                    self._cache_result(url, status, resp, use_cache, method)
                    return _from_resp(resp)
                time.sleep(_backoff(attempt))
                continue

            self._circuit.on_success()
            self._cache_result(url, status, resp, use_cache, method)
            return _from_resp(resp)

        if last_exc:
            raise last_exc
        raise RuntimeError(f"request failed without exception: {method} {url}")

    def _cache_result(self, url: str, status: int, resp, use_cache: bool, method: str) -> None:
        """Cache any GET response (2xx and errors) so a rerun never re-hits TM.

        Known-bad URLs (403 blocks) are remembered for the cache TTL — the
        resume-cache contract is "check first, don't re-request".
        """
        if use_cache and method == "GET" and self._cache:
            try:
                self._cache.put(url, status, resp.content)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("cache put failed: %s", exc)

    # --- conveniences ---------------------------------------------------------
    def api(self, path: str, **kwargs) -> Any:
        """GET a local API endpoint and return parsed JSON."""
        url = f"{self.api_base}/{path.lstrip('/')}"
        resp = self.request("GET", url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_html(self, url: str, **kwargs) -> _Response:
        """GET a direct Transfermarkt HTML page (mitarbeiter, etc.)."""
        return self.request("GET", url, bucket="html", **kwargs)

    def get_binary(self, url: str, **kwargs) -> _Response:
        """GET a CDN resource (image)."""
        bucket = "cdn" if _is_cdn(url) else "html"
        return self.request("GET", url, bucket=bucket, use_cache=False, **kwargs)


def _from_resp(resp) -> _Response:
    try:
        return _Response(resp.status_code, resp.content, resp.url)
    except Exception:
        return _Response(resp.status_code, resp.text.encode("utf-8"), resp.url)


def _close(resp) -> None:
    try:
        resp.close()
    except Exception:
        pass


def _backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    return min(cap, base * (2 ** attempt)) + random.uniform(0, base)


def _retry_after(value: str | None, default: float) -> float:
    if not value:
        return default
    value = value.strip()
    if value.isdigit():
        return float(value)
    return default  # HTTP-date is not parsed, fall back to default


def _is_cdn(url: str) -> bool:
    return any(host in url for host in CDN_HOSTS)


def _is_direct_tm(url: str) -> bool:
    return url.startswith("https://www.transfermarkt")


def _is_blocked_direct(resp, limit: int = 5000) -> bool:
    """Detect a Cloudflare/anti-bot challenge page by content signature."""
    if 200 <= resp.status_code < 300:
        try:
            head = resp.content[:limit].lower()
            return any(sig in head for sig in BLOCK_SIGNATURES)
        except Exception:
            return False
    return False


# Module-level singleton shared by fetch modules and workers.
client = Client()