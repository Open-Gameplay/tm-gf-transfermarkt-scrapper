"""Step 1: Fetch league club lists for ALL leagues.

Discovers all competition IDs from the TM national competitions index,
fetches /competitions/{id}/clubs for each. Results go to resume cache.

Usage:
    python fetch_leagues.py                  # all leagues from TM index
    python fetch_leagues.py --leagues GB1 ES1 # specific leagues only
"""

from __future__ import annotations

import argparse
import logging

from bs4 import BeautifulSoup

from client import Client

logger = logging.getLogger("fetch_leagues")

INDEX_URL = "https://www.transfermarkt.com/wettbewerbe/national/"


def parse_competition_index(client: Client) -> list[str]:
    """Parse the national competitions index page -> league ids."""
    resp = client.get_html(INDEX_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    ids: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/wettbewerb/" in href:
            lid = href.split("/wettbewerb/")[-1].split("/")[0]
            if lid not in ids:
                ids.append(lid)
    logger.info("TM index: %d competitions", len(ids))
    return ids


def fetch_league_clubs(client: Client, league_ids: list[str]) -> dict:
    result = {}
    for lid in league_ids:
        try:
            data = client.api(f"competitions/{lid}/clubs")
            clubs = data.get("clubs") or []
            result[lid] = clubs
            logger.info("%s (%s): %d clubs", data.get("name", lid), lid, len(clubs))
        except Exception as exc:
            logger.warning("competition %s failed: %s", lid, exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch club lists for all leagues.")
    parser.add_argument("--leagues", nargs="*", default=None,
                        help="competition IDs (default: all from TM index)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()
    if args.leagues:
        leagues = args.leagues
    else:
        leagues = parse_competition_index(client)
    result = fetch_league_clubs(client, leagues)

    total_clubs = 0
    for lid, clubs in result.items():
        total_clubs += len(clubs)
    logger.info("done: %d leagues, %d clubs total", len(result), total_clubs)


if __name__ == "__main__":
    main()
