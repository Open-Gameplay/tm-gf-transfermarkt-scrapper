"""Crawl orchestrator: fetch Tier-1 (or pilot) data and build the canon.

Usage:
    python crawl.py --scope pilot                # default: PSG, Real Madrid, France
    python crawl.py --scope tier1                # full Tier-1 coverage
    python crawl.py --spot 5                     # fetch spot profiles/market values for 5 players
    python crawl.py --images                     # also download images after building canon

Every HTTP request goes through client.py (rate-limited + resume-cached), so a
crawl can be interrupted and resumed: already-fetched URLs never hit TM again.
"""

from __future__ import annotations

import argparse
import logging
import random

from build_canon import build_canon
from config import (
    PILOT_CLUB_IDS,
    PILOT_NATIONAL_TEAM_IDS,
    TIER_1_COMPETITIONS,
    TIER_1_NATIONAL_TEAMS,
)
from fetch import clubs, coaches, competitions, market_values, profiles, rosters

logger = logging.getLogger("scraper.crawl")


def resolve_club_ids(scope: str) -> list[str]:
    if scope == "pilot":
        return list(PILOT_CLUB_IDS)
    club_ids: list[str] = []
    comps = competitions.search_competitions(TIER_1_COMPETITIONS)
    for comp in comps:
        clbs = competitions.competition_clubs(comp["id"])
        club_ids.extend(c["id"] for c in clbs)
        logger.info("competition %s (%s): %d clubs", comp["name"], comp["id"], len(clbs))
    return sorted(set(club_ids))


def resolve_team_ids(scope: str) -> list[str]:
    if scope == "pilot":
        return list(PILOT_NATIONAL_TEAM_IDS)
    from client import client as singleton
    data = singleton.api("national-teams/most-valuable")
    wanted = {name.strip().lower() for name in TIER_1_NATIONAL_TEAMS}
    team_ids = [
        r["id"] for r in (data.get("results") or [])
        if (r.get("name") or "").strip().lower() in wanted
    ]
    logger.info("resolved %d national teams", len(team_ids))
    return team_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl Transfermarkt data and build canon JSON.")
    parser.add_argument("--scope", choices=["pilot", "tier1"], default="pilot")
    parser.add_argument("--spot", type=int, default=5,
                        help="number of players to fetch spot profiles/market values for (0 = none)")
    parser.add_argument("--images", action="store_true", help="download images after building canon")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("scope=%s spot=%d images=%s", args.scope, args.spot, args.images)

    club_ids = resolve_club_ids(args.scope)
    team_ids = resolve_team_ids(args.scope)
    logger.info("clubs=%d national_teams=%d", len(club_ids), len(team_ids))

    # Rosters & profiles (primary source: one request per team).
    logger.info("fetching club profiles ...")
    club_profiles = clubs.club_profiles(club_ids)
    logger.info("fetching club rosters ...")
    club_rosters = rosters.club_rosters(club_ids)
    logger.info("fetching national team metadata + rosters ...")
    nt_profiles = rosters.national_team_profiles(team_ids)
    nt_rosters = rosters.national_team_rosters(team_ids)

    # Coach ids from direct /mitarbeiter/ parse, then coach profiles via API.
    logger.info("fetching coach ids from /mitarbeiter/ ...")
    coach_map = coaches.coach_ids_for_teams(team_ids)
    coach_ids = [cid for cid in coach_map.values() if cid]
    logger.info("fetching coach profiles ...")
    coach_profiles = coaches.coach_profiles(coach_ids)

    # Spot profiles / market values for a limited player subset (collection data).
    all_player_ids = sorted({
        p["id"]
        for roster in list(club_rosters.values()) + list(nt_rosters.values())
        for p in roster
        if p.get("id")
    })
    logger.info("players in scope: %d", len(all_player_ids))
    spot_ids = random.sample(all_player_ids, min(args.spot, len(all_player_ids))) if args.spot else []
    player_profiles = profiles.player_profiles(spot_ids) if spot_ids else {}
    market_values = market_values.player_market_values(spot_ids) if spot_ids else {}
    logger.info("spot profiles=%d market_values=%d", len(player_profiles), len(market_values))

    build_canon(
        club_rosters=club_rosters,
        club_profiles=club_profiles,
        nt_rosters=nt_rosters,
        nt_profiles=nt_profiles,
        coach_map=coach_map,
        coach_profiles=coach_profiles,
        player_profiles=player_profiles,
        market_values=market_values,
    )

    if args.images:
        from download_images import download_canon_images
        download_canon_images(club_profiles, nt_profiles, player_profiles)

    logger.info("crawl finished")


if __name__ == "__main__":
    main()