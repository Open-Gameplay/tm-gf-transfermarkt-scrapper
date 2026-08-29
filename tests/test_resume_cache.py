"""ResumeCache: SQLite roundtrip, TTL, survival across restart (port from upgraded suite)."""

import time

from cache import ResumeCache


def test_put_get_roundtrip(tmp_path):
    c = ResumeCache(str(tmp_path / "c.sqlite3"), ttl=3600)
    c.put("http://x/a", 200, b"hello")
    assert c.get("http://x/a") == (200, b"hello")


def test_miss_returns_none(tmp_path):
    c = ResumeCache(str(tmp_path / "c.sqlite3"), ttl=3600)
    assert c.get("http://x/missing") is None


def test_ttl_expiry(tmp_path):
    c = ResumeCache(str(tmp_path / "c.sqlite3"), ttl=0)
    c.put("http://x/a", 200, b"hello")
    time.sleep(1.1)
    assert c.get("http://x/a") is None


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "c.sqlite3")
    ResumeCache(path, ttl=3600).put("http://x/a", 200, b"persisted")
    assert ResumeCache(path, ttl=3600).get("http://x/a") == (200, b"persisted")


def test_keyed_by_url_only(tmp_path):
    """The design keys the cache by URL alone (requests.url PRIMARY KEY)."""
    c = ResumeCache(str(tmp_path / "c.sqlite3"), ttl=3600)
    c.put("http://x/a", 200, b"g")
    assert c.get("http://x/a") == (200, b"g")


def test_caches_error_statuses(tmp_path):
    """Known-bad responses (e.g. 403 blocks) are cached so reruns never re-hit TM."""
    c = ResumeCache(str(tmp_path / "c.sqlite3"), ttl=3600)
    c.put("http://x/blocked", 403, b"")
    assert c.get("http://x/blocked") == (403, b"")