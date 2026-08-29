"""Fetch TM club rosters for ALL competitions (height enrichment source).

Scans the Transfermarkt national-competitions index (direct page, curl_cffi),
then for every competition fetches the club list and each club's roster via the
local API — all cached in the resume cache, so it is resumable and a rerun only
picks up what is missing. Rosters are NOT the blocked profile pages.

These rosters provide `height` (and club/nationality) for the open-football
join in load_openfootball.py.

Usage:
    python fetch_all_rosters.py            # all leagues from the TM index
    env: TM_INDEX_URL (default /wettbewerbe/national/), scope via --limit
"""

from __future__ import annotations

import argparse
import logging
import re

from bs4 import BeautifulSoup

from client import Client

logger = logging.getLogger("scraper.fetch_all_rosters")

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch all TM club rosters into the resume cache.")
    parser.add_argument("--limit", type=int, default=0,
                        help="only process the first N competitions (0 = all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()
    league_ids = parse_competition_index(client)
    if args.limit:
        league_ids = league_ids[: args.limit]
        logger.info("limited to first %d competitions", args.limit)

    club_total = 0
    roster_total = 0
    for i, lid in enumerate(league_ids, 1):
        try:
            data = client.api(f"competitions/{lid}/clubs")
        except Exception as exc:
            logger.warning("competition %s clubs failed: %s", lid, exc)
            continue
        club_ids = [c["id"] for c in (data.get("clubs") or [])]
        club_total += len(club_ids)
        for cid in club_ids:
            try:
                rd = client.api(f"clubs/{cid}/players")
                roster_total += len(rd.get("players") or [])
            except Exception as exc:
                logger.warning("club %s roster failed: %s", cid, exc)
        if i % 20 == 0 or i == len(league_ids):
            logger.info("competitions %d/%d | clubs=%d rosters=%d",
                        i, len(league_ids), club_total, roster_total)

    logger.info("done: %d competitions, %d clubs, %d roster players",
                len(league_ids), club_total, roster_total)


if __name__ == "__main__":
    main()