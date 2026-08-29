"""Assemble the canon JSON on TM-id from fetched records.

Pure and idempotent: reads the records passed in, never mutates them and never
touches the resume cache. Output files land in `data/canon/` and are validated
against canon_schema.json.

Input records are the raw API payloads from the fetch modules:
    club_rosters:  {club_id: [player, ...]}            (clubs/{id}/players)
    club_profiles: {club_id: profile}                  (clubs/{id}/profile)
    nt_rosters:    {team_id: [player, ...]}            (national-teams/{id}/players)
    nt_profiles:   {team_id: profile}                  (national-teams/{id}/profile)
    coach_map:     {team_id: coach_id | None}          (mitarbeiter parse)
    coach_profiles:{coach_id: profile}                 (coaches/{id}/profile)
    player_profiles:{player_id: profile}               (players/{id}/profile, optional)
    market_values: {player_id: market_value}           (players/{id}/market_value, optional)
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

import jsonschema

from config import CANON_DIR, SCHEMA_PATH

logger = logging.getLogger("scraper.build_canon")

CANON_VERSION = "1.0.0"

FILES = ("meta", "competitions", "clubs", "national_teams", "players",
         "players_market_value", "coaches")


# --- small helpers -----------------------------------------------------------
def _first_last(name: str | None) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    parts = name.strip().split(" ", 1)
    return parts[0], (parts[1] if len(parts) > 1 else None)


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    digits = "".join(ch for ch in str(value) if ch.isdigit() or ch in ".,")
    digits = digits.replace(",", "").replace(".", "")
    return int(digits) if digits else None


def _max_market_value(mv: dict | None) -> int | None:
    if not mv:
        return None
    values = [
        h.get("marketValue")
        for h in (mv.get("marketValueHistory") or [])
        if isinstance(h.get("marketValue"), int)
    ]
    return max(values) if values else None


# --- assemblers --------------------------------------------------------------
def _build_meta(records: dict[str, int]) -> dict:
    return {
        "version": CANON_VERSION,
        "snapshot_date": dt.date.today().isoformat(),
        "tm_season": None,
        "counts": records,
    }


def _build_competitions(club_profiles: dict[str, dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for profile in club_profiles.values():
        league = profile.get("league") or {}
        cid = league.get("id")
        if not cid:
            continue
        seen[cid] = {
            "id": cid,
            "name": league.get("name"),
            "type": "league",
            "country": {
                "id": league.get("countryId"),
                "name": league.get("countryName"),
            },
            "tier": _to_int(league.get("tier")),
            "logo": None,
        }
    return list(seen.values())


def _build_clubs(club_profiles: dict[str, dict]) -> list[dict]:
    clubs = []
    for profile in club_profiles.values():
        clubs.append({
            "id": profile.get("id"),
            "name": profile.get("name"),
            "colors": profile.get("colors") or [],
            "stadium": {
                "name": profile.get("stadiumName"),
                "seats": _to_int(profile.get("stadiumSeats")),
            },
            "league_id": (profile.get("league") or {}).get("id"),
            "logo": profile.get("image"),
            "founded_on": profile.get("foundedOn"),
        })
    return clubs


def _build_national_teams(nt_profiles, coach_map) -> list[dict]:
    teams = []
    for tid, profile in nt_profiles.items():
        teams.append({
            "id": tid,
            "name": profile.get("name"),
            "coach_id": coach_map.get(tid),
            "flag": None,
            "emblem": profile.get("image"),
        })
    return teams


def _build_players(club_rosters, nt_rosters, player_profiles, market_values) -> list[dict]:
    players: dict[str, dict] = {}

    # Club rosters are the primary source (richest per-player data).
    for club_id, roster in club_rosters.items():
        for p in roster:
            pid = p.get("id")
            if not pid:
                continue
            entry = {
                "id": pid,
                "name": p.get("name"),
                "birth_date": p.get("dateOfBirth"),
                "height": p.get("height"),
                "foot": p.get("foot"),
                "citizenship": p.get("nationality") or [],
                "position": {"main": p.get("position"), "other": []},
                "club_id": club_id,
                "national_team_id": None,
                "shirt_number": None,
                "market_value": p.get("marketValue") if isinstance(p.get("marketValue"), int) else None,
                "max_market_value": None,
                "outfitter": None,
                "is_retired": False,
            }
            _merge_player(players, entry)

    # National team rosters add national_team_id + shirt_number (+ fallback data).
    for team_id, roster in nt_rosters.items():
        for p in roster:
            pid = p.get("id")
            if not pid:
                continue
            entry = {
                "id": pid,
                "name": p.get("name"),
                "birth_date": None,
                "height": None,
                "foot": None,
                "citizenship": [],
                "position": {"main": p.get("position"), "other": []},
                "club_id": None,
                "national_team_id": team_id,
                "shirt_number": p.get("shirtNumber"),
                "market_value": p.get("marketValue") if isinstance(p.get("marketValue"), int) else None,
                "max_market_value": None,
                "outfitter": None,
                "is_retired": False,
            }
            _merge_player(players, entry)

    # Spot profiles add outfitter / is_retired / multi-position.
    for pid, profile in player_profiles.items():
        if pid not in players:
            players[pid] = {
                "id": pid, "name": profile.get("name"), "birth_date": None,
                "height": profile.get("height"), "foot": profile.get("foot"),
                "citizenship": profile.get("citizenship") or [],
                "position": {"main": None, "other": []},
                "club_id": None, "national_team_id": None, "shirt_number": None,
                "market_value": None, "max_market_value": None,
                "outfitter": profile.get("outfitter"), "is_retired": bool(profile.get("isRetired")),
            }
        entry = players[pid]
        if entry.get("outfitter") is None:
            entry["outfitter"] = profile.get("outfitter")
        if not entry.get("is_retired") and profile.get("isRetired"):
            entry["is_retired"] = True
        pos = profile.get("position") or {}
        other = [x for x in (pos.get("other") or []) if x]
        if other:
            entry["position"]["other"] = other
        if entry["position"]["main"] is None and pos.get("main"):
            entry["position"]["main"] = pos.get("main")

    out = []
    for entry in players.values():
        first, last = _first_last(entry["name"])
        entry["first_name"] = first
        entry["last_name"] = last
        mv = market_values.get(entry["id"]) or {}
        entry["max_market_value"] = _max_market_value(mv)
        if entry.get("market_value") is None and isinstance(mv.get("marketValue"), int):
            entry["market_value"] = mv["marketValue"]
        out.append(entry)
    return out


def _merge_player(players: dict[str, dict], entry: dict) -> None:
    pid = entry["id"]
    if pid in players:
        cur = players[pid]
        # Fill gaps, keep non-null values.
        for key in ("name", "birth_date", "height", "foot", "market_value"):
            if cur.get(key) is None and entry.get(key) is not None:
                cur[key] = entry[key]
        if cur.get("position", {}).get("main") is None:
            cur["position"]["main"] = entry["position"]["main"]
        if not cur.get("citizenship") and entry.get("citizenship"):
            cur["citizenship"] = entry["citizenship"]
        if cur.get("club_id") is None:
            cur["club_id"] = entry.get("club_id")
        if cur.get("national_team_id") is None:
            cur["national_team_id"] = entry.get("national_team_id")
        if cur.get("shirt_number") is None:
            cur["shirt_number"] = entry.get("shirt_number")
        return
    players[pid] = entry


def _build_market_values(market_values: dict[str, dict]) -> list[dict]:
    out = []
    for pid, mv in market_values.items():
        history = mv.get("marketValueHistory") or []
        cleaned = [
            {
                "age": h.get("age"),
                "date": h.get("date"),
                "clubId": h.get("clubId"),
                "clubName": h.get("clubName"),
                "marketValue": h.get("marketValue") if isinstance(h.get("marketValue"), int) else None,
            }
            for h in history
        ]
        out.append({"id": pid, "history": cleaned})
    return out


def _build_coaches(coach_profiles: dict[str, dict]) -> list[dict]:
    out = []
    for cid, profile in coach_profiles.items():
        current_club = profile.get("current_club") or {}
        out.append({
            "id": cid,
            "name": profile.get("name"),
            "citizenship": profile.get("citizenship"),
            "current_club_id": current_club.get("id"),
        })
    return out


# --- entry points ------------------------------------------------------------
def build_canon(
    *,
    club_rosters: dict[str, list[dict]] | None = None,
    club_profiles: dict[str, dict] | None = None,
    nt_rosters: dict[str, list[dict]] | None = None,
    nt_profiles: dict[str, dict] | None = None,
    coach_map: dict[str, str | None] | None = None,
    coach_profiles: dict[str, dict] | None = None,
    player_profiles: dict[str, dict] | None = None,
    market_values: dict[str, dict] | None = None,
    out_dir: str | Path = CANON_DIR,
) -> dict[str, Any]:
    """Assemble and write the canon files, then validate them."""
    club_profiles = club_profiles or {}
    nt_profiles = nt_profiles or {}
    coach_profiles = coach_profiles or {}

    canon = {
        "meta": _build_meta({
            "competitions": len(_build_competitions(club_profiles)),
            "clubs": len(club_profiles),
            "national_teams": len(nt_profiles),
            "players": len(_build_players(club_rosters or {}, nt_rosters or {},
                                          player_profiles or {}, market_values or {})),
            "coaches": len(coach_profiles),
        }),
        "competitions": _build_competitions(club_profiles),
        "clubs": _build_clubs(club_profiles),
        "national_teams": _build_national_teams(nt_profiles, coach_map or {}),
        "players": _build_players(club_rosters or {}, nt_rosters or {},
                                      player_profiles or {}, market_values or {}),
        "players_market_value": _build_market_values(market_values or {}),
        "coaches": _build_coaches(coach_profiles),
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for key in FILES:
        path = out_dir / f"{key}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(canon[key], f, ensure_ascii=False, indent=2)
        logger.info("wrote %s (%d entries)", path.name, len(canon[key]) if isinstance(canon[key], list) else "-")

    _validate(canon)
    return canon


def _validate(canon: dict[str, Any]) -> None:
    """Validate every canon file against canon_schema.json."""
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    definition = {
        "meta": "meta",
        "competitions": "competition",
        "clubs": "club",
        "national_teams": "national_team",
        "players": "player",
        "players_market_value": "market_value_entry",
        "coaches": "coach",
    }
    for key in FILES:
        sub = schema["definitions"][definition[key]]
        instance = canon[key]
        if key == "meta":
            jsonschema.validate(instance=instance, schema=sub)
        else:
            for item in instance:
                jsonschema.validate(instance=item, schema=sub)
    logger.info("canon validated against canon_schema.json (7 files OK)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("build_canon is a library; run crawl.py to produce the canon.")