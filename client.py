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

import json
import logging
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import requests

from cache import ResumeCache
from config import (
    API_BASE,
    BLOCK_RATIO_HIGH,
    BLOCK_RATIO_MID,
    BLOCK_WINDOW,
    CACHE_ENABLED,
    CACHE_PATH,
    CACHE_TTL,
    CDN_BURST,
    CDN_RPS,
    CDN_RPS_MIN,
    CIRCUIT_FAILS,
    CIRCUIT_PAUSE,
    CONCURRENCY,
    DEFAULT_HEADERS,
    HTML_BURST,
    HTML_RPS,
    HTML_RPS_MIN,
    HTTP_MAX_RETRIES,
    HTTP_TIMEOUT,
    RATE_DROP_FACTOR,
    RATE_GROW_AFTER,
    RATE_GROW_FACTOR,
    RATES_PATH,
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
    """Thread-safe token bucket: `rps` tokens/sec, capacity = burst.

    A single request can consume several tokens via `weight` — used for
    endpoints that cost more than one live TM request per call (e.g.
    /players/{id}/market_value fetches the page AND the chart API = 2).
    """

    def __init__(self, rps: float, burst: int):
        self.rps = max(rps, 0.01)
        self.capacity = max(burst, 1)
        self.tokens = float(self.capacity)
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, weight: float = 1.0) -> None:
        weight = max(weight, 1.0)
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rps)
                self.last = now
                if self.tokens >= weight:
                    self.tokens -= weight
                    return
                wait = (weight - self.tokens) / self.rps
            # jitter: irregular spacing looks human; the bucket self-corrects the
            # long-run average because tokens accumulate from the real elapsed time
            time.sleep(wait * (0.6 + 0.8 * random.random()))

    def set_rate(self, rps: float) -> None:
        """Change the refill rate at runtime (used by the adaptive rate aimer)."""
        with self.lock:
            self.rps = max(rps, 0.01)
            self.tokens = min(self.tokens, self.capacity)


class CircuitBreaker:
    """After `threshold` consecutive failures, pause all requests for a while.

    The pause grows exponentially on repeated openings (120s -> 4m -> 8m -> ...
    up to `pause * 16`) and resets on the first success — so a persistent TM
    block makes us wait properly instead of hammering every 2 minutes.
    """

    def __init__(self, threshold: int, pause: float):
        self.threshold = max(threshold, 1)
        self.pause = pause
        self.fails = 0
        self.open_count = 0
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
            self.open_count = 0
            self.opened_until = 0.0

    def on_failure(self) -> None:
        with self.lock:
            self.fails += 1
            if self.fails >= self.threshold:
                self.open_count += 1
                wait = min(self.pause * (2 ** (self.open_count - 1)), self.pause * 16)
                self.opened_until = time.monotonic() + wait
                logger.error("Circuit breaker OPEN for %.1fs (open #%d, %d consecutive failures)",
                             wait, self.open_count, self.fails)


class AdaptiveSemaphore:
    """AIMD concurrency control.

    After `ai_after_ok` consecutive successes the concurrency ceiling grows by
    one (up to `max_v`); after any throttle (429/5xx or connection error) it is
    halved (multiplicative decrease, not below `min_v`). The token bucket still
    enforces the global rate, so AIMD only tunes how many requests are in flight.
    """

    def __init__(self, init: int, min_v: int, max_v: int, ai_after_ok: int):
        self.min_v = max(min_v, 1)
        self.max_v = max(max_v, self.min_v)
        self.permits = max(min(init, self.max_v), self.min_v)
        self.in_use = 0
        self.ok_streak = 0
        self.ai_after_ok = ai_after_ok
        self.cv = threading.Condition()

    def __enter__(self) -> "AdaptiveSemaphore":
        with self.cv:
            while self.in_use >= self.permits:
                self.cv.wait()
            self.in_use += 1
        return self

    def __exit__(self, *exc_info) -> None:
        with self.cv:
            self.in_use -= 1
            self.cv.notify()

    def on_success(self) -> None:
        with self.cv:
            self.ok_streak += 1
            if self.ok_streak >= self.ai_after_ok and self.permits < self.max_v:
                self.permits += 1
                self.ok_streak = 0
                logger.info("AIMD: concurrency +1 -> %d", self.permits)
                self.cv.notify_all()

    def on_throttle(self) -> None:
        with self.cv:
            self.ok_streak = 0
            new_permits = max(self.permits // 2, self.min_v)
            if new_permits < self.permits:
                logger.warning("AIMD: concurrency %d -> %d (throttled)", self.permits, new_permits)
                self.permits = new_permits


class RateAimer:
    """AIMD on the request RATE (tokens/sec), complementing the concurrency AIMD.

    On a throttle event the rate is halved (down to `min_rps`); after
    `grow_after` consecutive successes it grows by `grow_factor` (up to
    `max_rps`). The current rate is persisted via `on_change`, so the next
    session starts where this one ended — remembering TM's constraints instead
    of blindly re-testing the configured max.
    """

    def __init__(
        self,
        start_rps: float,
        max_rps: float,
        *,
        min_rps: float = 1.0,
        grow_after: int = 50,
        grow_factor: float = 1.25,
        drop_factor: float = 0.5,
        on_change=None,
    ):
        self.rate = max(min(start_rps, max_rps), min_rps)
        self.max_rps = max_rps
        self.min_rps = min_rps
        self.grow_after = max(grow_after, 1)
        self.grow_factor = grow_factor
        self.drop_factor = drop_factor
        self.on_change = on_change
        self._ok = 0
        self.lock = threading.Lock()

    def on_success(self) -> bool:
        """Count a success; grow the rate once enough accumulated. Returns True if changed."""
        changed = False
        with self.lock:
            self._ok += 1
            if self._ok >= self.grow_after and self.rate < self.max_rps:
                self._ok = 0
                new = min(self.max_rps, self.rate * self.grow_factor)
                if new != self.rate:
                    logger.info("rate AIMD: %.2f -> %.2f rps (grew)", self.rate, new)
                    self.rate = new
                    changed = True
        self._notify(changed)
        return changed

    def on_throttle(self) -> bool:
        """Halve the rate (floor at min_rps). Returns True if changed."""
        changed = False
        with self.lock:
            self._ok = 0
            new = max(self.min_rps, self.rate * self.drop_factor)
            if new != self.rate:
                logger.warning("rate AIMD: %.2f -> %.2f rps (throttled)", self.rate, new)
                self.rate = new
                changed = True
        self._notify(changed)
        return changed

    def _notify(self, changed: bool) -> None:
        if changed and self.on_change:
            try:
                self.on_change()
            except Exception as exc:  # pragma: no cover - persistence must not break requests
                logger.debug("rate persist failed: %s", exc)


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
        cache: ResumeCache | None | bool = None,
        adaptive_rate: bool = True,
        rates_path: str | Path | None = RATES_PATH,
        html_rps_min: float = HTML_RPS_MIN,
        cdn_rps_min: float = CDN_RPS_MIN,
    ):
        self.api_base = api_base
        self.timeout = timeout
        self.max_retries = max_retries
        self._adaptive_rate = adaptive_rate
        self._rates_path = Path(rates_path) if rates_path else None
        self._circuit = CircuitBreaker(circuit_fails, circuit_pause)
        if cache is False:
            self._cache = None
        else:
            self._cache = cache if cache is not None else (
                ResumeCache(CACHE_PATH, CACHE_TTL) if CACHE_ENABLED else None
            )
        # AIMD concurrency: starts at `concurrency`, grows to `concurrency*2` on
        # success, halves on throttle. Token bucket still caps the global rate.
        self._sem = AdaptiveSemaphore(concurrency, 1, max(concurrency * 2, 4), ai_after_ok=50)
        self._outcomes: dict[str, deque] = {}
        self._lock = threading.Lock()

        # AIMD on the rate: each bucket gets a RateAimer that halves the rps on
        # throttle and slowly grows back to the configured max. Learned rates are
        # persisted so the next session starts where this one ended.
        saved = self._load_rates() if (adaptive_rate and self._rates_path) else {}
        self._aimers: dict[str, RateAimer] = {}
        self._buckets: dict[str, TokenBucket] = {}
        for name, rps, rps_min, burst in (
            ("html", html_rps, html_rps_min, html_burst),
            ("cdn", cdn_rps, cdn_rps_min, cdn_burst),
        ):
            if adaptive_rate:
                start = min(rps, float(saved.get(name, rps)))
                aimer = RateAimer(
                    start, rps, min_rps=rps_min,
                    grow_after=RATE_GROW_AFTER,
                    grow_factor=RATE_GROW_FACTOR,
                    drop_factor=RATE_DROP_FACTOR,
                    on_change=self._persist_rates if self._rates_path else None,
                )
                self._aimers[name] = aimer
                self._buckets[name] = TokenBucket(aimer.rate, burst)
            else:
                self._buckets[name] = TokenBucket(rps, burst)

        self._local = threading.local()
        logger.info(
            "client: backend=%s html=%.2frps cdn=%.2frps adaptive=%s cache=%s",
            BACKEND, self._buckets["html"].rps, self._buckets["cdn"].rps,
            "on" if adaptive_rate else "off", "on" if self._cache else "off",
        )

    def _load_rates(self) -> dict[str, float]:
        try:
            with open(self._rates_path, encoding="utf-8") as f:
                data = json.load(f)
            return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _persist_rates(self) -> None:
        if not self._rates_path:
            return
        try:
            self._rates_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._rates_path, "w", encoding="utf-8") as f:
                json.dump({name: round(aimer.rate, 2) for name, aimer in self._aimers.items()},
                          f, indent=2)
        except OSError as exc:  # pragma: no cover - persistence must not break requests
            logger.debug("rate persist failed: %s", exc)

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
        weight: float = 1.0,
        use_cache: bool = True,
        timeout: float | None = None,
        max_retries: int | None = None,
        **kwargs,
    ) -> _Response:
        """Perform one request with cache, rate limit, circuit and retries.

        `weight` is the number of live TM requests a single call costs (the
        rate limiter consumes that many tokens), so expensive endpoints are
        automatically paced slower within the same global budget.
        """
        method = method.upper()
        timeout = timeout or self.timeout
        max_retries = max_retries or self.max_retries

        if use_cache and method == "GET" and self._cache:
            hit = self._cache.get(url)
            if hit and 200 <= hit[0] < 300:
                return _Response(hit[0], hit[1], url)

        if bucket not in self._buckets:
            bucket = "html"

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            self._circuit.gate()
            self._buckets[bucket].acquire(weight)

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
                    self._throttle(bucket)
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(_backoff(attempt))
                    continue
                except Exception as exc:  # curl_cffi has its own exceptions
                    last_exc = exc
                    self._throttle(bucket)
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(_backoff(attempt))
                    continue

            status = resp.status_code

            if status == 403:
                self._note_block(bucket)
                self._cache_result(url, status, resp, use_cache, method)
                return _from_resp(resp)

            if status == 429 or status in RETRY_STATUSES:
                self._throttle(bucket)
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
                self._throttle(bucket)
                if attempt == max_retries - 1:
                    self._cache_result(url, status, resp, use_cache, method)
                    return _from_resp(resp)
                time.sleep(_backoff(attempt))
                continue

            self._success(bucket)
            self._cache_result(url, status, resp, use_cache, method)
            return _from_resp(resp)

        if last_exc:
            raise last_exc
        raise RuntimeError(f"request failed without exception: {method} {url}")

    def _throttle(self, bucket: str) -> None:
        """React to a TM-side throttle: halve concurrency AND the bucket rate."""
        self._circuit.on_failure()
        self._sem.on_throttle()
        aimer = self._aimers.get(bucket)
        if aimer and aimer.on_throttle():
            self._buckets[bucket].set_rate(aimer.rate)

    def _note_block(self, bucket: str) -> None:
        """React to a 403 block by its recent ratio (sliding window).

        - high ratio (>= BLOCK_RATIO_HIGH): sustained block -> open the circuit
          (stop hammering; the circuit pause lets TM's window reset);
        - mid ratio: clustered blocks -> halve the rate, keep going (the retry
          queue catches the blocked players);
        - low ratio: scattered honeypots -> do nothing special.
        """
        with self._lock:
            dq = self._outcomes.setdefault(bucket, deque(maxlen=BLOCK_WINDOW))
            dq.append(False)
            ratio = dq.count(False) / max(len(dq), 1)
        # only act once the window has enough samples for the ratio to be meaningful
        if len(dq) < max(5, BLOCK_WINDOW // 2):
            return
        if ratio >= BLOCK_RATIO_HIGH:
            logger.warning("sustained block (403 ratio %.0f%%) on %s -> opening circuit",
                           ratio * 100, bucket)
            self._circuit.on_failure()
            self._sem.on_throttle()
            aimer = self._aimers.get(bucket)
            if aimer and aimer.on_throttle():
                self._buckets[bucket].set_rate(aimer.rate)
        elif ratio >= BLOCK_RATIO_MID:
            logger.warning("block ratio %.0f%% on %s -> halving rate",
                           ratio * 100, bucket)
            self._sem.on_throttle()
            aimer = self._aimers.get(bucket)
            if aimer and aimer.on_throttle():
                self._buckets[bucket].set_rate(aimer.rate)

    def _success(self, bucket: str) -> None:
        """React to a successful request: reset circuit, maybe grow the rate."""
        self._circuit.on_success()
        self._sem.on_success()
        with self._lock:
            dq = self._outcomes.setdefault(bucket, deque(maxlen=BLOCK_WINDOW))
            dq.append(True)
        aimer = self._aimers.get(bucket)
        if aimer and aimer.on_success():
            self._buckets[bucket].set_rate(aimer.rate)

    def _cache_result(self, url: str, status: int, resp, use_cache: bool, method: str) -> None:
        """Cache GET responses so a rerun never re-hits TM for SUCCESSES.

        Only 2xx responses are cached (resume). 403/404 blocks and transient
        429/5xx are NOT cached: a block may clear, and the retry queue (plus a
        rerun) re-tries them instead of skipping — so no player is left without
        data just because TM was blocking at fetch time.
        """
        if not (use_cache and method == "GET" and self._cache):
            return
        if not (200 <= status < 300):
            return
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