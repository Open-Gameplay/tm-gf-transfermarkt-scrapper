"""Fetch club profiles (colors, stadium, league, logo) for ALL clubs in the cache.

Reads club IDs from the resume cache (/competitions/{id}/clubs responses), then
for every club fetches /clubs/{id}/profile via the local API — all cached in the
resume cache, so it is resumable and a rerun only picks up what is missing.

Club profiles are NOT the blocked player profile pages — they are safe to fetch.

Usage:
    python fetch_all_club_profiles.py
    python fetch_all_club_profiles.py --limit 50   # first 50 clubs only
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3

from client import Client
from config import CACHE_PATH

logger = logging.getLogger("scraper.fetch_all_club_profiles")


def collect_all_club_ids(client: Client) -> list[str]:
    """Extract all unique club IDs from /competitions/{id}/clubs cache entries."""
    club_ids: list[str] = []
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests "
            "WHERE url LIKE '%/competitions/%/clubs' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cannot read resume cache: %s", exc)
        return club_ids

    for (payload,) in rows:
        try:
            clubs = json.loads(payload).get("clubs") or []
        except (json.JSONDecodeError, TypeError):
            continue
        for c in clubs:
            cid = c.get("id")
            if cid and cid not in club_ids:
                club_ids.append(cid)

    logger.info("collected %d unique club IDs from resume cache", len(club_ids))
    return club_ids


def _cached_has_profile(client: Client, club_id: str) -> bool:
    """True if the cached club profile has meaningful data (colors or league)."""
    cache = client._cache
    if not cache:
        return False
    hit = cache.get(f"{client.api_base}/clubs/{club_id}/profile")
    if not hit:
        return False
    try:
        data = json.loads(hit[1])
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(data.get("colors") or data.get("league"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch club profiles for ALL clubs in the resume cache."
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="only process the first N clubs (0 = all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()
    club_ids = collect_all_club_ids(client)
    if args.limit:
        club_ids = club_ids[: args.limit]
        logger.info("limited to first %d clubs", args.limit)

    total = len(club_ids)
    ok = 0
    skip = 0
    fail = 0

    for i, cid in enumerate(club_ids, 1):
        if _cached_has_profile(client, cid):
            skip += 1
            continue
        try:
            client.api(f"clubs/{cid}/profile")
            ok += 1
        except Exception as exc:
            logger.warning("club profile %s failed: %s", cid, exc)
            fail += 1
        if i % 100 == 0 or i == total:
            logger.info("progress %d/%d | ok=%d skip=%d fail=%d",
                        i, total, ok, skip, fail)

    logger.info("done: %d clubs, %d fetched, %d skipped (cached), %d failed",
                total, ok, skip, fail)


if __name__ == "__main__":
    main()
