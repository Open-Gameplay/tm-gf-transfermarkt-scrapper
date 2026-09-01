"""Step 2: Fetch club profiles for ALL leagues.

Reads club IDs from resume cache (fetch_leagues.py must run first),
fetches /clubs/{id}/profile for each. Results go to resume cache.

Usage:
    python fetch_club_profiles.py
    python fetch_club_profiles.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3

from client import Client
from config import CACHE_PATH

logger = logging.getLogger("fetch_club_profiles")


def collect_ids() -> list[str]:
    club_ids: list[str] = []
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/competitions/%/clubs' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cannot read cache: %s", exc)
        return club_ids
    for (payload,) in rows:
        try:
            data = json.loads(payload)
            for c in data.get("clubs") or []:
                cid = c.get("id")
                if cid and cid not in club_ids:
                    club_ids.append(cid)
        except (json.JSONDecodeError, TypeError):
            continue
    logger.info("collected %d clubs", len(club_ids))
    return club_ids


def _cached(client: Client, cid: str) -> bool:
    cache = client._cache
    if not cache:
        return False
    hit = cache.get(f"{client.api_base}/clubs/{cid}/profile")
    if not hit:
        return False
    try:
        data = json.loads(hit[1])
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(data.get("colors") or data.get("league"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch club profiles for pilot leagues.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()
    club_ids = collect_ids()
    if args.limit:
        club_ids = club_ids[: args.limit]

    total = len(club_ids)
    ok = skip = fail = 0
    for i, cid in enumerate(club_ids, 1):
        if _cached(client, cid):
            skip += 1
            continue
        try:
            client.api(f"clubs/{cid}/profile")
            ok += 1
        except Exception as exc:
            logger.warning("club %s failed: %s", cid, exc)
            fail += 1
        if i % 20 == 0 or i == total:
            logger.info("progress %d/%d | ok=%d skip=%d fail=%d", i, total, ok, skip, fail)
    logger.info("done: %d clubs, %d fetched, %d cached, %d failed", total, ok, skip, fail)


if __name__ == "__main__":
    main()
