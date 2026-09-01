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
import json
import logging
import re

from bs4 import BeautifulSoup

from client import Client

logger = logging.getLogger("scraper.fetch_all_rosters")

INDEX_URL = "https://www.transfermarkt.com/wettbewerbe/national/"


def _cached_has_photos(client: Client, club_id: str) -> bool:
    """True if the cached roster already carries player portraits."""
    cache = client._cache
    if not cache:
        return False
    hit = cache.get(f"{client.api_base}/clubs/{club_id}/players")
    if not hit:
        return False
    try:
        data = json.loads(hit[1])
    except (json.JSONDecodeError, TypeError):
        return False
    return any(p.get("imageUrl") for p in (data.get("players") or []))


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
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch rosters whose cached payload lacks imageUrl (photo backfill)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()
    league_ids = parse_competition_index(client)
    if args.limit:
        league_ids = league_ids[: args.limit]
        logger.info("limited to first %d competitions", args.limit)

    club_total = 0
    roster_total = 0
    refetched = 0
    for i, lid in enumerate(league_ids, 1):
        try:
            data = client.api(f"competitions/{lid}/clubs")
        except Exception as exc:
            logger.warning("competition %s clubs failed: %s", lid, exc)
            continue
        club_ids = [c["id"] for c in (data.get("clubs") or [])]
        club_total += len(club_ids)
        for cid in club_ids:
            if args.refresh and _cached_has_photos(client, cid):
                continue
            try:
                if args.refresh:
                    # Fetch fresh data (bypass cache) then manually cache it
                    rd = client.api(f"clubs/{cid}/players", use_cache=False)
                    url = f"{client.api_base}/clubs/{cid}/players"
                    import json as _json
                    client._cache.put(url, 200, _json.dumps(rd).encode("utf-8"))
                else:
                    rd = client.api(f"clubs/{cid}/players")
                roster_total += len(rd.get("players") or [])
                if args.refresh:
                    refetched += 1
            except Exception as exc:
                logger.warning("club %s roster failed: %s", cid, exc)
        if i % 20 == 0 or i == len(league_ids):
            logger.info("competitions %d/%d | clubs=%d rosters=%d%s",
                        i, len(league_ids), club_total, roster_total,
                        f" refetched={refetched}" if args.refresh else "")

    logger.info("done: %d competitions, %d clubs, %d roster players%s",
                len(league_ids), club_total, roster_total,
                f" (refetched {refetched})" if args.refresh else "")


if __name__ == "__main__":
    main()