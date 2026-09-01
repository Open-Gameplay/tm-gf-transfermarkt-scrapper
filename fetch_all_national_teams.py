"""Fetch ALL national teams: profiles + rosters.

Uses /national-teams/most-valuable (internally paginates all pages) to discover
every national team ID, then fetches each team's profile and roster. All results
are cached in the resume cache — resumable and idempotent.

National team endpoints are NOT blocked by TM.

Usage:
    python fetch_all_national_teams.py
    python fetch_all_national_teams.py --limit 20   # first 20 teams only
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3

from client import Client
from config import CACHE_PATH

logger = logging.getLogger("scraper.fetch_all_national_teams")

MOST_VALUABLE_URL = "national-teams/most-valuable"


def discover_team_ids(client: Client) -> list[dict]:
    """Get all national team IDs from /most-valuable (API paginates internally)."""
    data = client.api(MOST_VALUABLE_URL)
    results = data.get("results") or []
    teams = []
    seen = set()
    for r in results:
        tid = r.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            teams.append({"id": tid, "name": r.get("name"), "country": r.get("country")})
    logger.info("discovered %d national teams from most-valuable", len(teams))
    return teams


def _cached_has(client: Client, url_key: str) -> bool:
    cache = client._cache
    if not cache:
        return False
    hit = cache.get(url_key)
    if not hit:
        return False
    try:
        data = json.loads(hit[1])
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch ALL national team profiles and rosters."
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="only process the first N teams (0 = all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()
    teams = discover_team_ids(client)
    if args.limit:
        teams = teams[: args.limit]
        logger.info("limited to first %d teams", args.limit)

    total = len(teams)
    profile_ok = 0
    profile_skip = 0
    roster_ok = 0
    roster_skip = 0
    fail = 0

    for i, team in enumerate(teams, 1):
        tid = team["id"]
        base = f"{client.api_base}/national-teams/{tid}"

        # Profile
        if _cached_has(client, f"{base}/profile"):
            profile_skip += 1
        else:
            try:
                client.api(f"national-teams/{tid}/profile")
                profile_ok += 1
            except Exception as exc:
                logger.warning("NT profile %s (%s) failed: %s", tid, team.get("name"), exc)
                fail += 1

        # Roster
        if _cached_has(client, f"{base}/players"):
            roster_skip += 1
        else:
            try:
                client.api(f"national-teams/{tid}/players")
                roster_ok += 1
            except Exception as exc:
                logger.warning("NT roster %s (%s) failed: %s", tid, team.get("name"), exc)
                fail += 1

        if i % 20 == 0 or i == total:
            logger.info("progress %d/%d | profiles: ok=%d skip=%d | rosters: ok=%d skip=%d | fail=%d",
                        i, total, profile_ok, profile_skip, roster_ok, roster_skip, fail)

    logger.info("done: %d teams | profiles: %d fetched, %d cached | rosters: %d fetched, %d cached | %d failed",
                total, profile_ok, profile_skip, roster_ok, roster_skip, fail)


if __name__ == "__main__":
    main()
