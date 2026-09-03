"""Fetch clubs for leagues, saving to data/clubs.json (unique by club ID).

Reads data/leagues.json (metadata) and data/tournament_structure.json
to determine which leagues are downloadable.

Usage:
    python fetch_clubs.py                    # fetch all missing
    python fetch_clubs.py --batch 50         # fetch 50 at a time
    python fetch_clubs.py --country Russia   # fetch only one country
    python fetch_clubs.py --reset            # clear clubs and start over
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from client import Client

logger = logging.getLogger("fetch_clubs")
LEAGUES_PATH = Path("data/leagues.json")
CLUBS_PATH = Path("data/clubs.json")
STRUCTURE_PATH = Path("data/tournament_structure.json")
BATCH_DEFAULT = 50


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_downloadable_leagues(structure: dict, leagues: dict, country: str | None = None) -> list[str]:
    """Return league IDs that are downloadable based on tournament structure."""
    downloadable = set()
    countries = structure.get("countries", {})

    for c_name, c_data in countries.items():
        if country and c_name.lower() != country.lower():
            continue
        for tier_info in c_data.get("tiers", []):
            lid = tier_info["league_id"]
            if lid in leagues:
                downloadable.add(lid)

    return sorted(downloadable)


def fetch_clubs_batch(client: Client, league_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch /competitions/{id}/clubs for each league, return {id: clubs}."""
    result: dict[str, list[dict]] = {}
    for lid in league_ids:
        try:
            data = client.api(f"competitions/{lid}/clubs")
            clubs = data.get("clubs") or []
            result[lid] = clubs
            logger.info("%s (%s): %d clubs", data.get("name", lid), lid, len(clubs))
        except Exception as exc:
            logger.warning("competition %s failed: %s", lid, exc)
            result[lid] = []
    return result


def merge_clubs(clubs: dict, fetched: dict[str, list[dict]]) -> int:
    """Merge fetched clubs into clubs.json format. Returns number of new clubs."""
    new_count = 0
    for lid, club_list in fetched.items():
        for c in club_list:
            cid = c["id"]
            if cid not in clubs:
                clubs[cid] = {"id": cid, "name": c["name"], "leagues": []}
                new_count += 1
            if lid not in clubs[cid]["leagues"]:
                clubs[cid]["leagues"].append(lid)
    return new_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch clubs for leagues.")
    parser.add_argument("--batch", type=int, default=BATCH_DEFAULT,
                        help="leagues per batch (default: %(default)s)")
    parser.add_argument("--country", type=str, default=None,
                        help="fetch only for this country")
    parser.add_argument("--reset", action="store_true",
                        help="clear clubs.json and start over")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    leagues = load_json(LEAGUES_PATH)["leagues"]
    structure = load_json(STRUCTURE_PATH)

    if args.reset:
        save_json(CLUBS_PATH, {})
        logger.info("cleared clubs.json")

    clubs = load_json(CLUBS_PATH) if CLUBS_PATH.exists() else {}
    downloadable = get_downloadable_leagues(structure, leagues, args.country)

    # Filter out already-fetched leagues
    done = set()
    for cid, c_info in clubs.items():
        for lid in c_info.get("leagues", []):
            done.add(lid)

    remaining = [lid for lid in downloadable if lid not in done]
    logger.info(
        "downloadable=%d done=%d remaining=%d",
        len(downloadable), len(done), len(remaining),
    )

    if not remaining:
        logger.info("all clubs already fetched")
        return

    client = Client()
    batch = remaining[: args.batch]
    logger.info("fetching batch of %d ...", len(batch))

    fetched = fetch_clubs_batch(client, batch)
    new = merge_clubs(clubs, fetched)

    save_json(CLUBS_PATH, clubs)
    logger.info("saved (%d unique clubs, %d new)", len(clubs), new)

    still_remaining = len(remaining) - len(batch)
    if still_remaining > 0:
        logger.info("still remaining: %d (run again to continue)", still_remaining)
    else:
        logger.info("all done!")


if __name__ == "__main__":
    main()
