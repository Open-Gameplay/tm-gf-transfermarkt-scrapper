"""SQLite resume cache.

Keyed by URL, so a rerun never re-hits Transfermarkt for data already fetched.
A crawl can be interrupted and resumed safely.

Table: requests(url TEXT PRIMARY KEY, status INTEGER, payload BLOB, fetched_at INTEGER)
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class ResumeCache:
    def __init__(self, path: str | Path, ttl: int = 7 * 24 * 3600):
        self.path = str(path)
        self.ttl = ttl
        self.lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    url        TEXT PRIMARY KEY,
                    status     INTEGER,
                    payload    BLOB,
                    fetched_at INTEGER
                )
                """
            )
            con.execute("PRAGMA journal_mode=WAL")

    def get(self, url: str) -> tuple[int, bytes] | None:
        """Return (status, payload) for a fresh cache entry, or None."""
        with self.lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT status, payload, fetched_at FROM requests WHERE url = ?",
                    (url,),
                ).fetchone()
            finally:
                con.close()
        if not row:
            return None
        status, payload, fetched_at = row
        if time.time() - fetched_at > self.ttl:
            return None
        return status, payload

    def put(self, url: str, status: int, payload: bytes) -> None:
        with self.lock:
            con = self._connect()
            try:
                con.execute(
                    "INSERT OR REPLACE INTO requests (url, status, payload, fetched_at) "
                    "VALUES (?, ?, ?, ?)",
                    (url, status, payload, int(time.time())),
                )
                con.commit()
            finally:
                con.close()

    def count(self) -> int:
        with self.lock:
            con = self._connect()
            try:
                row = con.execute("SELECT COUNT(*) FROM requests").fetchone()
            finally:
                con.close()
        return int(row[0]) if row else 0

    def summary(self) -> dict[str, int]:
        """Count of entries grouped by status (for reporting)."""
        with self.lock:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT status, COUNT(*) FROM requests GROUP BY status"
                ).fetchall()
            finally:
                con.close()
        return {int(status): count for status, count in rows}