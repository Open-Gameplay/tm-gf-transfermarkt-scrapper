"""CircuitBreaker: opens at threshold, pauses, resets on success (port from upgraded suite)."""

import time

from client import CircuitBreaker


def test_initial_closed():
    cb = CircuitBreaker(threshold=3, pause=0.1)
    cb.gate()  # must not block


def test_opens_after_threshold():
    cb = CircuitBreaker(threshold=3, pause=0.3)
    cb.on_failure()
    cb.on_failure()
    t0 = time.monotonic()
    cb.gate()  # not open yet - does not wait
    assert time.monotonic() - t0 < 0.05
    cb.on_failure()  # 3rd - opens
    t0 = time.monotonic()
    cb.gate()
    assert 0.25 <= time.monotonic() - t0 <= 0.6


def test_success_resets():
    cb = CircuitBreaker(threshold=3, pause=0.1)
    cb.on_failure()
    cb.on_failure()
    cb.on_success()
    cb.on_failure()
    cb.on_failure()  # below threshold - counter was reset
    t0 = time.monotonic()
    cb.gate()
    assert time.monotonic() - t0 < 0.05


def test_recovers_after_pause():
    cb = CircuitBreaker(threshold=1, pause=0.2)
    cb.on_failure()
    cb.gate()  # waits 0.2s
    t0 = time.monotonic()
    cb.gate()  # timer already expired
    assert time.monotonic() - t0 < 0.05