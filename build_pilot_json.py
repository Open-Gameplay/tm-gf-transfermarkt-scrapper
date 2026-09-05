"""Step 6: Build full JSON from resume cache.

Reads all cached data, assembles into structured JSON files.
Works with ALL leagues (not just pilot).

Usage:
    python build_pilot_json.py
    python build_pilot_json.py --out data/full
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from pathlib import Path

from config import CACHE_PATH, ROOT

logger = logging.getLogger("build_pilot_json")


def _load_cache() -> list[tuple[str, str]]:
    con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
    rows = con.execute("SELECT url, payload FROM requests WHERE status=200").fetchall()
    con.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build full JSON from cache.")
    parser.add_argument("--out", default=str(ROOT / "data" / "full"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_cache()
    logger.info("loaded %d cache entries", len(rows))

    # Index data
    comp_clubs = {}  # lid -> clubs
    club_profiles = {}  # cid -> profile
    club_rosters = {}  # cid -> players
    nt_profiles = {}  # tid -> profile
    nt_rosters = {}  # tid -> players

    for url, payload in rows:
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue

        if "/competitions/" in url and "/clubs" in url:
            lid = data.get("id", "")
            if lid:
                comp_clubs[lid] = data.get("clubs") or []

        elif "/clubs/" in url and url.endswith("/players"):
            m = re.search(r"/clubs/(\d+)/players", url)
            if m:
                club_rosters[m.group(1)] = data.get("players") or []

        elif "/clubs/" in url and "/profile" in url:
            m = re.search(r"/clubs/(\d+)/profile", url)
            if m:
                club_profiles[m.group(1)] = data

        elif "/national-teams/" in url and "/profile" in url:
            m = re.search(r"/national-teams/(\d+)/profile", url)
            if m:
                nt_profiles[m.group(1)] = data

        elif "/national-teams/" in url and "/players" in url:
            m = re.search(r"/national-teams/(\d+)/players", url)
            if m:
                nt_rosters[m.group(1)] = data.get("players") or []

    # Build player index from club rosters
    player_index = {}  # pid -> player data from club
    for cid, players in club_rosters.items():
        for p in players:
            pid = p.get("id")
            if pid:
                player_index[pid] = p

    # Build countries from club profiles (group by country)
    # API returns league.countryId / league.countryName (flat, not nested)
    country_leagues = {}  # country_id -> {name, leagues: {lid -> league_data}}
    for cid, profile in club_profiles.items():
        league_info = profile.get("league") or {}
        country_id = str(league_info.get("countryId", ""))
        country_name = league_info.get("countryName", "")
        lid = league_info.get("id", "")

        if not country_id or not lid:
            continue

        if country_id not in country_leagues:
            country_leagues[country_id] = {"name": country_name, "leagues": {}}

        if lid not in country_leagues[country_id]["leagues"]:
            country_leagues[country_id]["leagues"][lid] = {
                "id": lid,
                "name": league_info.get("name", lid),
                "tier": league_info.get("tier"),
                "country_id": country_id,
                "clubs": [],
            }

    # Add clubs to their leagues
    for lid, clubs in comp_clubs.items():
        for club_info in clubs:
            cid = club_info.get("id")
            profile = club_profiles.get(cid, {})
            league_info = profile.get("league") or {}
            country_id = str(league_info.get("countryId", ""))
            actual_lid = league_info.get("id", lid)

            if country_id in country_leagues and actual_lid in country_leagues[country_id]["leagues"]:
                roster = club_rosters.get(cid, [])
                country_leagues[country_id]["leagues"][actual_lid]["clubs"].append({
                    "id": cid,
                    "name": club_info.get("name"),
                    "official_name": profile.get("officialName"),
                    "logo_url": profile.get("image"),
                    "colors": profile.get("colors", []),
                    "stadium": profile.get("stadiumName"),
                    "stadium_seats": profile.get("stadiumSeats"),
                    "founded": profile.get("foundedOn"),
                    "squad": profile.get("squad"),
                    "players": roster,
                })

    # Build countries list
    countries = []
    for cid, cinfo in sorted(country_leagues.items()):
        leagues_list = []
        for lid, ldata in sorted(cinfo["leagues"].items()):
            leagues_list.append({
                "id": lid,
                "logo_url": f"https://img.a.transfermarkt.technology/logo/header/{lid.lower()}.png",
                "country_id": cid,
                "name": ldata["name"],
                "tier": ldata["tier"],
                "clubs": ldata["clubs"],
            })
        countries.append({
            "id": cid,
            "name": cinfo["name"],
            "flag_url": f"https://img.a.transfermarkt.technology/flagge/tiny/{cid}.png",
            "leagues": leagues_list,
        })

    # Build national teams
    national_teams = []
    for tid in sorted(nt_profiles.keys()):
        profile = nt_profiles.get(tid, {})
        roster = nt_rosters.get(tid, [])
        enriched = []
        for p in roster:
            pid = p.get("id")
            club_data = player_index.get(pid, {})
            enriched.append({
                "id": pid,
                "name": p.get("name"),
                "shirt_number": p.get("shirtNumber"),
                "position": p.get("position"),
                "age": p.get("age"),
                "club": p.get("club"),
                "market_value": p.get("marketValue"),
                "height": p.get("height") or club_data.get("height"),
                "foot": p.get("foot") or club_data.get("foot"),
                "date_of_birth": p.get("dateOfBirth") or club_data.get("dateOfBirth"),
                "nationality": club_data.get("nationality"),
                "image_url": p.get("imageUrl") or club_data.get("imageUrl"),
            })
        national_teams.append({
            "id": tid,
            "name": profile.get("name"),
            "emblem_url": profile.get("image"),
            "colors": profile.get("colors", []),
            "founded": profile.get("foundedOn"),
            "players": enriched,
        })

    # Write files
    (out_dir / "clubs.json").write_text(
        json.dumps(countries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "national_teams.json").write_text(
        json.dumps(national_teams, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Summary
    total_clubs = sum(len(l["clubs"]) for c in countries for l in c["leagues"])
    total_players = sum(len(cl["players"]) for c in countries for l in c["leagues"] for cl in l["clubs"])
    total_nt = len(national_teams)
    total_nt_players = sum(len(nt["players"]) for nt in national_teams)

    logger.info("written to %s", out_dir)
    logger.info("countries: %d", len(countries))
    logger.info("clubs: %d", total_clubs)
    logger.info("players: %d", total_players)
    logger.info("national teams: %d", total_nt)
    logger.info("NT players: %d", total_nt_players)


if __name__ == "__main__":
    main()
