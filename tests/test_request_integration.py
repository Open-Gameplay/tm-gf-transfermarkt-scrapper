"""Integration tests: Client.request against a local mock HTTP server (port from upgraded suite)."""

import time

from cache import ResumeCache
from client import AdaptiveSemaphore, Client


def _client(tmp_path, **kw) -> Client:
    base = dict(
        html_rps=100, cdn_rps=100, max_retries=3,
        concurrency=4, circuit_fails=999,  # circuit disabled by default in tests
        adaptive_rate=False, rates_path=None,  # no rate aimer / no persistence
        cache=ResumeCache(str(tmp_path / "c.sqlite3"), ttl=3600),
    )
    base.update(kw)
    return Client(**base)


def test_simple_200(mock_server, tmp_path):
    mock_server.scenario = [(200, b"hello", {})]
    resp = _client(tmp_path).request("GET", mock_server.url + "/x")
    assert resp.status_code == 200
    assert resp.content == b"hello"


def test_retries_429_then_succeeds(mock_server, tmp_path):
    mock_server.scenario = [
        (429, b"slow", {"Retry-After": "0"}),
        (200, b"ok", {}),
    ]
    resp = _client(tmp_path).request("GET", mock_server.url + "/x", use_cache=False)
    assert resp.status_code == 200
    assert len(mock_server.requests) == 2


def test_respects_retry_after(mock_server, tmp_path):
    mock_server.scenario = [
        (429, b"slow", {"Retry-After": "1"}),
        (200, b"ok", {}),
    ]
    t0 = time.monotonic()
    resp = _client(tmp_path).request("GET", mock_server.url + "/x", use_cache=False)
    elapsed = time.monotonic() - t0
    assert resp.status_code == 200
    assert elapsed >= 1.0, f"should have waited >=1s, waited {elapsed:.2f}"


def test_503_retried(mock_server, tmp_path):
    mock_server.scenario = [
        (503, b"down", {}),
        (502, b"down", {}),
        (200, b"ok", {}),
    ]
    resp = _client(tmp_path).request("GET", mock_server.url + "/x", use_cache=False)
    assert resp.status_code == 200
    assert len(mock_server.requests) == 3


def test_aimd_throttles_on_429(mock_server, tmp_path):
    c = _client(tmp_path, concurrency=8)
    c._sem = AdaptiveSemaphore(8, 1, 8, 50)
    mock_server.scenario = [
        (429, b"x", {"Retry-After": "0"}),
        (200, b"ok", {}),
    ]
    c.request("GET", mock_server.url + "/x", use_cache=False)
    assert c._sem.permits == 4, f"AIMD should cut 8->4, got {c._sem.permits}"


def test_aimd_grows_on_success(mock_server, tmp_path):
    c = _client(tmp_path)
    c._sem = AdaptiveSemaphore(2, 1, 8, ai_after_ok=3)
    for i in range(3):
        mock_server.scenario.append((200, f"ok{i}".encode(), {}))
    for i in range(3):
        c.request("GET", mock_server.url + f"/x{i}", use_cache=False)
    assert c._sem.permits == 3


def test_cache_hit_skips_network(mock_server, tmp_path):
    c = _client(tmp_path)
    mock_server.scenario = [(200, b"first", {})]
    r1 = c.request("GET", mock_server.url + "/cached")
    r2 = c.request("GET", mock_server.url + "/cached")
    assert r1.content == r2.content == b"first"
    assert len(mock_server.requests) == 1, "second request should hit the cache"


def test_cache_disabled_per_request(mock_server, tmp_path):
    c = _client(tmp_path, cache=False)
    mock_server.scenario = [(200, b"a", {}), (200, b"b", {})]
    c.request("GET", mock_server.url + "/x", use_cache=False)
    c.request("GET", mock_server.url + "/x", use_cache=False)
    assert len(mock_server.requests) == 2


def test_caches_error_statuses(mock_server, tmp_path):
    """A 403 is NOT cached — it must be retried (the retry queue / a rerun)."""
    c = _client(tmp_path)
    mock_server.scenario = [(403, b"blocked", {}), (403, b"blocked", {})]
    c.request("GET", mock_server.url + "/blocked")
    c.request("GET", mock_server.url + "/blocked")
    assert len(mock_server.requests) == 2, "403 must not be cached as done"


def test_cache_hit_only_serves_2xx(mock_server, tmp_path):
    """A stale non-2xx entry in the resume cache must be ignored (retried)."""
    c = _client(tmp_path)
    c._cache.put(mock_server.url + "/stale403", 403, b"")
    mock_server.scenario = [(403, b"blocked", {})]
    c.request("GET", mock_server.url + "/stale403")
    assert len(mock_server.requests) == 1, "stale cached 403 must be re-fetched"


def test_429_is_not_cached(mock_server, tmp_path):
    """429 is transient (rate limit) — must not be frozen in the resume cache."""
    c = _client(tmp_path, max_retries=1)
    mock_server.scenario = [(429, b"slow", {"Retry-After": "0"})]
    resp = c.request("GET", mock_server.url + "/flaky")
    assert resp.status_code == 429  # retries exhausted
    assert c._cache.get(mock_server.url + "/flaky") is None, "429 must not be cached"


def test_user_agent_is_realistic(mock_server, tmp_path):
    from config import USER_AGENTS

    c = _client(tmp_path)
    mock_server.scenario = [(200, b"ok", {})]
    c.request("GET", mock_server.url + "/x")
    ua = mock_server.requests[0]["headers"].get("User-Agent", "")
    assert ua in USER_AGENTS, f"UA not from the pool: {ua}"


def test_circuit_breaker_opens_on_repeated_failures(mock_server, tmp_path):
    c = _client(tmp_path, circuit_fails=2, circuit_pause=0.1)
    for _ in range(10):
        mock_server.scenario.append((500, b"err", {}))
    c.request("GET", mock_server.url + "/x", max_retries=3)
    assert c._circuit.opened_until > 0, "circuit should open"


def test_sustained_403_halves_rate_and_opens_circuit(mock_server, tmp_path):
    """Near-100% 403 over the window = sustained block -> halve rate AND open the circuit."""
    c = Client(html_rps=8.0, html_rps_min=0.5, adaptive_rate=True, rates_path=None,
               max_retries=1, circuit_fails=2, circuit_pause=0.1, cache=False)
    for i in range(12):
        mock_server.scenario.append((403, b"blocked", {}))
    for i in range(12):
        c.request("GET", mock_server.url + f"/p{i}", use_cache=False)
    assert c._buckets["html"].rps < 8.0, "rate should be halved"
    assert c._circuit.opened_until > 0, "sustained block should open the circuit"


def test_clustered_403_halves_rate_without_circuit(mock_server, tmp_path):
    """~50% 403 = clustered blocks -> halve the rate but do NOT open the circuit."""
    c = Client(html_rps=8.0, html_rps_min=0.5, adaptive_rate=True, rates_path=None,
               max_retries=1, circuit_fails=2, circuit_pause=0.1, cache=False)
    for i in range(10):
        mock_server.scenario += [(200, b"ok", {}), (403, b"blocked", {})]
    for i in range(20):
        c.request("GET", mock_server.url + f"/p{i}", use_cache=False)
    assert c._buckets["html"].rps < 8.0, "clustered blocks should halve the rate"
    assert c._circuit.opened_until == 0, "circuit must NOT open on ~50% blocks"


def test_scattered_403_do_not_throttle(mock_server, tmp_path):
    """Low 403 ratio (scattered honeypots) -> no throttling at all."""
    c = Client(html_rps=8.0, html_rps_min=0.5, adaptive_rate=True, rates_path=None,
               max_retries=1, circuit_fails=2, circuit_pause=0.1, cache=False)
    # 3 blocks in 20 requests (ratio 0.15 < mid) — honeypots
    pattern = [200, 200, 403, 200, 200, 200, 200, 403, 200, 200,
               200, 200, 200, 200, 200, 200, 200, 200, 403, 200]
    for code in pattern:
        mock_server.scenario.append((code, b"x", {}))
    for i in range(20):
        c.request("GET", mock_server.url + f"/p{i}", use_cache=False)
    assert c._buckets["html"].rps == 8.0, "scattered honeypots must not throttle"
    assert c._circuit.opened_until == 0