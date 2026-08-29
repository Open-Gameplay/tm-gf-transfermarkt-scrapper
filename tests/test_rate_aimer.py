"""RateAimer: AIMD on the request rate + persistence of learned limits."""

import json

from client import RateAimer, TokenBucket


def test_initial_rate_clamped_to_max():
    r = RateAimer(start_rps=50, max_rps=8, min_rps=1.0)
    assert r.rate == 8.0
    r2 = RateAimer(start_rps=0.2, max_rps=8, min_rps=1.0)
    assert r2.rate == 1.0


def test_throttle_halves():
    r = RateAimer(start_rps=8, max_rps=8, min_rps=1.0)
    r.on_throttle()
    assert r.rate == 4.0
    r.on_throttle()
    assert r.rate == 2.0
    r.on_throttle()
    assert r.rate == 1.0
    r.on_throttle()
    assert r.rate == 1.0, "must not go below the floor"


def test_success_grows_slowly():
    r = RateAimer(start_rps=1, max_rps=8, min_rps=1.0, grow_after=50, grow_factor=1.25)
    for _ in range(49):
        assert not r.on_success()
    assert r.on_success()  # 50th -> grows
    assert r.rate == 1.25
    # never exceeds the max
    r2 = RateAimer(start_rps=7.5, max_rps=8, min_rps=1.0, grow_after=1, grow_factor=1.25)
    r2.on_success()
    assert r2.rate == 8.0
    r2.on_success()
    assert r2.rate == 8.0, "capped at max"


def test_throttle_resets_success_streak():
    r = RateAimer(start_rps=1, max_rps=8, min_rps=1.0, grow_after=3, grow_factor=1.25)
    r.on_success()
    r.on_success()
    r.on_throttle()  # resets streak; rate stays at floor 1.0
    assert not r.on_success()
    assert not r.on_success()
    assert r.on_success()  # streak must restart from 0


def test_persist_on_change(tmp_path):
    path = tmp_path / "rates.json"

    def persist():
        path.write_text(json.dumps({"html": r.rate}))

    r = RateAimer(start_rps=8, max_rps=8, min_rps=1.0, on_change=persist)
    assert not path.exists()
    r.on_throttle()
    assert json.loads(path.read_text()) == {"html": 4.0}


def test_set_rate_on_bucket():
    b = TokenBucket(rps=8, burst=4)
    b.set_rate(2.0)
    assert b.rps == 2.0
    b.set_rate(0.001)
    assert b.rps >= 0.01  # clamped floor