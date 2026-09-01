"""Step 4: Fetch ALL national team profiles and rosters.

Uses /national-teams/most-valuable (all pages, ~248 teams),
fetches profile + roster for each via the local API. Results go to resume cache.

Usage:
    python fetch_national_teams.py              # all (~248)
    python fetch_national_teams.py --top 20     # top 20 only
"""

from __future__ import annotations

import argparse
import logging

from client import Client

logger = logging.getLogger("fetch_national_teams")


def fetch_all_team_ids(client: Client, top_n: int) -> list[dict]:
    data = client.api("national-teams/most-valuable")
    results = data.get("results") or []
    teams = []
    seen = set()
    for r in results:
        tid = r.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            teams.append({"id": tid, "name": r.get("name"), "country": r.get("country")})
    return teams[:top_n]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch national team profiles and rosters.")
    parser.add_argument("--top", type=int, default=300)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()
    teams = fetch_all_team_ids(client, args.top)
    logger.info("fetched %d national team IDs", len(teams))

    total = len(teams)
    profile_ok = profile_skip = roster_ok = roster_skip = fail = 0
    for i, team in enumerate(teams, 1):
        tid = team["id"]
        base = f"{client.api_base}/national-teams/{tid}"

        # Profile
        hit = client._cache and client._cache.get(f"{base}/profile")
        if hit:
            profile_skip += 1
        else:
            try:
                client.api(f"national-teams/{tid}/profile")
                profile_ok += 1
            except Exception as exc:
                logger.warning("NT profile %s (%s) failed: %s", tid, team["name"], exc)
                fail += 1

        # Roster
        hit = client._cache and client._cache.get(f"{base}/players")
        if hit:
            roster_skip += 1
        else:
            try:
                client.api(f"national-teams/{tid}/players")
                roster_ok += 1
            except Exception as exc:
                logger.warning("NT roster %s (%s) failed: %s", tid, team["name"], exc)
                fail += 1

        if i % 10 == 0 or i == total:
            logger.info("progress %d/%d | profiles ok=%d skip=%d | rosters ok=%d skip=%d | fail=%d",
                        i, total, profile_ok, profile_skip, roster_ok, roster_skip, fail)

    logger.info("done: %d teams | profiles: %d fetched, %d cached | rosters: %d fetched, %d cached | %d failed",
                len(teams), profile_ok, profile_skip, roster_ok, roster_skip, fail)


if __name__ == "__main__":
    main()
