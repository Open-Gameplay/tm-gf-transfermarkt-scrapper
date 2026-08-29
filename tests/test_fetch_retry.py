"""Retry queue: failed per-player fetches are retried across rounds."""

import pytest

from fetch.market_values import player_market_values
from fetch.profiles import player_profiles


class _FlakyProfileClient:
    def __init__(self, fail_first=1):
        self.calls = {}
        self.fail_first = fail_first

    def api(self, path):
        pid = path.split("/")[1]
        self.calls[pid] = self.calls.get(pid, 0) + 1
        if self.calls[pid] <= self.fail_first:
            raise RuntimeError("blocked")
        return {"id": pid, "name": f"player {pid}"}


class _FakeResp:
    def __init__(self, data=None):
        self._data = data or {"list": [], "current": None}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FlakyMVClient:
    def __init__(self, fail_rounds=1):
        self.calls = {}
        self.fail_rounds = fail_rounds

    def api(self, path, **kw):
        pid = path.split("/")[1]
        self.calls[pid] = self.calls.get(pid, 0) + 1
        if self.calls[pid] <= self.fail_rounds:
            raise RuntimeError("api blocked")
        return {"id": pid, "marketValue": 1, "marketValueHistory": []}

    def get_html(self, url, **kw):
        pid = url.rstrip("/").split("/")[-1]
        self.calls[pid] = self.calls.get(pid, 0) + 1
        if self.calls[pid] <= self.fail_rounds + 1:
            raise RuntimeError("ceapi blocked")
        return _FakeResp()


def test_profiles_retry_failures_across_rounds():
    client = _FlakyProfileClient(fail_first=1)  # each pid fails once, then OK
    out = player_profiles(["1", "2", "3"], client=client, rounds=3, cooldown=0)
    assert set(out) == {"1", "2", "3"}
    assert all(client.calls[p] == 2 for p in ("1", "2", "3"))


def test_profiles_exhaust_rounds_keep_pending():
    client = _FlakyProfileClient(fail_first=99)  # always fails
    out = player_profiles(["1", "2"], client=client, rounds=2, cooldown=0)
    assert out == {}
    assert client.calls["1"] == 2  # two attempts, both failed


def test_market_values_retry_after_api_and_direct_fail():
    client = _FlakyMVClient(fail_rounds=1)
    out = player_market_values(["5"], client=client, rounds=3, cooldown=0)
    assert set(out) == {"5"}
    assert out["5"]["marketValue"] == 1


def test_market_values_pending_when_all_fail():
    client = _FlakyMVClient(fail_rounds=99)
    out = player_market_values(["7"], client=client, rounds=2, cooldown=0)
    assert out == {}