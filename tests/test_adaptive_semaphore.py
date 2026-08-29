"""AdaptiveSemaphore: AIMD logic and real concurrency limits (port from upgraded suite)."""

import threading
import time

from client import AdaptiveSemaphore


def test_initial_permits():
    sem = AdaptiveSemaphore(init=4, min_v=1, max_v=8, ai_after_ok=10)
    assert sem.permits == 4


def test_throttle_halves_permits():
    sem = AdaptiveSemaphore(init=8, min_v=1, max_v=8, ai_after_ok=10)
    sem.on_throttle(); assert sem.permits == 4
    sem.on_throttle(); assert sem.permits == 2
    sem.on_throttle(); assert sem.permits == 1
    sem.on_throttle(); assert sem.permits == 1, "must not go below min"


def test_success_grows_permits_after_threshold():
    sem = AdaptiveSemaphore(init=2, min_v=1, max_v=4, ai_after_ok=3)
    for _ in range(3):
        sem.on_success()
    assert sem.permits == 3
    for _ in range(3):
        sem.on_success()
    assert sem.permits == 4
    for _ in range(3):
        sem.on_success()
    assert sem.permits == 4, "must not exceed max"


def test_throttle_resets_streak():
    sem = AdaptiveSemaphore(init=2, min_v=1, max_v=4, ai_after_ok=3)
    sem.on_success()
    sem.on_success()
    sem.on_throttle()  # streak -> 0, permits -> 1
    sem.on_success()
    sem.on_success()
    assert sem.permits == 1, "streak was not reset"


def test_actually_limits_concurrency():
    """With permits=2 only 2 requests run concurrently."""
    sem = AdaptiveSemaphore(init=2, min_v=1, max_v=4, ai_after_ok=100)
    in_use_max = [0]
    in_use = [0]
    lock = threading.Lock()

    def worker():
        with sem:
            with lock:
                in_use[0] += 1
                in_use_max[0] = max(in_use_max[0], in_use[0])
            time.sleep(0.1)
            with lock:
                in_use[0] -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert in_use_max[0] == 2, f"max in_use = {in_use_max[0]}, expected 2"