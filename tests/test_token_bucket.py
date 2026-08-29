"""TokenBucket: mathematical accuracy under load (port from upgraded suite)."""

import threading
import time

from client import TokenBucket


def test_burst_consumed_immediately():
    b = TokenBucket(rps=10, burst=5)
    t0 = time.monotonic()
    for _ in range(5):
        b.acquire()
    assert time.monotonic() - t0 < 0.05, "burst must be spent without waiting"


def test_steady_rate():
    rps = 5.0
    b = TokenBucket(rps=rps, burst=1)
    t0 = time.monotonic()
    n = 6
    for _ in range(n):
        b.acquire()
    elapsed = time.monotonic() - t0
    expected = (n - 1) / rps  # first is instant (burst), rest every 1/rps
    assert abs(elapsed - expected) < 0.15, f"elapsed={elapsed:.2f}s, expected~{expected:.2f}s"


def test_thread_safe_under_contention():
    rps = 10.0
    b = TokenBucket(rps=rps, burst=2)
    n_threads = 8
    per_thread = 5
    total = n_threads * per_thread
    t0 = time.monotonic()

    def worker():
        for _ in range(per_thread):
            b.acquire()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    elapsed = time.monotonic() - t0
    expected_min = (total - 2) / rps  # minus burst
    assert elapsed >= expected_min - 0.5, f"rps violated under load: {elapsed:.2f}s vs >= {expected_min:.2f}s"
    assert elapsed < expected_min + 2.0, f"too slow: {elapsed:.2f}s"


def test_burst_refills_over_time():
    b = TokenBucket(rps=10, burst=3)
    for _ in range(3):
        b.acquire()
    time.sleep(0.5)  # ~5 tokens accumulate, capped at burst=3
    t0 = time.monotonic()
    for _ in range(3):
        b.acquire()
    assert time.monotonic() - t0 < 0.05


def test_weight_consumes_multiple_tokens():
    """A weight=2 request (e.g. market_value: page + chart) spends 2 tokens."""
    b = TokenBucket(rps=10, burst=5)
    b.acquire(weight=3)
    t0 = time.monotonic()
    b.acquire(weight=3)  # only 2 tokens left -> must wait for 1 more (0.1s)
    assert time.monotonic() - t0 >= 0.09
    b2 = TokenBucket(rps=100, burst=10)
    t0 = time.monotonic()
    b2.acquire(weight=2)
    b2.acquire(weight=2)
    assert time.monotonic() - t0 < 0.05  # burst covers both