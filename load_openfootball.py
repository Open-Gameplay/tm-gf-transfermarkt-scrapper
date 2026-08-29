"""Load the open-football-database (git repo) and merge it with our TM roster data.

open-football-database (https://github.com/ZOXEXIVO/open-football-database) is a
maintained Transfermarkt-derived dataset: clubs, leagues and players with
attributes (positions+levels, CA/PA, market value, foots, contract, TM-id).

TM club rosters from our resume cache provide the missing physical data:
height (TM profiles are blocked, rosters are not), plus club/nationality.

Join key: the player's `ids.transfermarkt.com` -> TM player id.

Usage:
    python load_openfootball.py --countries gb,es
    env: OF_DATABASE_PATH, OF_OUTPUT (default data/openfootball)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from pathlib import Path

from config import CACHE_PATH, ROOT

logger = logging.getLogger("scraper.load_openfootball")

OF_DATABASE_PATH = Path(os.getenv("OF_DATABASE_PATH", str(ROOT.parent / "open-football-database")))
OF_OUTPUT = Path(os.getenv("OF_OUTPUT", str(ROOT / "data" / "openfootball")))

# FM-style position codes used by open-football -> GF-ish role labels.
POSITION_MAP = {
    "GK": "Goalkeeper",
    "DC": "Centre-Back",
    "DL": "Left-Back",
    "DR": "Right-Back",
    "WBL": "Left Wing-Back",
    "WBR": "Right Wing-Back",
    "DM": "Defensive Midfield",
    "MC": "Central Midfield",
    "ML": "Left Midfield",
    "MR": "Right Midfield",
    "AMC": "Attacking Midfield",
    "AML": "Attacking Midfield Left",
    "AMR": "Attacking Midfield Right",
    "ST": "Centre-Forward",
}


def _foot(player: dict) -> str | None:
    f = player.get("foots")
    if isinstance(f, dict):
        # {left: 70, right: 100} — dominant foot is the higher value
        lv = f.get("left", 0)
        rv = f.get("right", 0)
        if lv == rv:
            return None
        return "left" if lv > rv else "right"
    if isinstance(f, list) and f:
        return f[0] if len(f) == 1 else (None if len(f) == 2 else f[0])
    return None


def load_openfootball_players(countries: list[str], path: Path) -> list[dict]:
    """Read all players for the given countries from the open-football repo."""
    players: list[dict] = []
    for cc in countries:
        cc_dir = path / "data" / cc
        if not cc_dir.is_dir():
            logger.warning("country %s not found in %s", cc, path)
            continue
        for league_dir in sorted(d for d in cc_dir.iterdir() if d.is_dir()):
            if league_dir.name == "free_agents":
                continue
            league = _load_json(league_dir / "league.json") or {}
            for club_dir in sorted(d for d in league_dir.iterdir() if d.is_dir()):
                club = _load_json(club_dir / "club.json") or _load_json(league_dir / f"{club_dir.name}.json") or {}
                club_name = club.get("name") or club_dir.name
                pdir = club_dir / "players"
                if not pdir.is_dir():
                    continue
                for pf in sorted(pdir.glob("*.json")):
                    p = _load_json(pf)
                    if not p:
                        continue
                    tm_id = (p.get("ids") or {}).get("transfermarkt.com")
                    players.append({
                        "openfootball_id": p.get("id"),
                        "tm_id": str(tm_id) if tm_id else None,
                        "name": f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
                        "first_name": p.get("first_name"),
                        "last_name": p.get("last_name"),
                        "birth_date": p.get("birth_date"),
                        "country_id": p.get("country_id"),
                        "positions": [
                            {"code": pos.get("code"), "level": pos.get("level"),
                             "role": POSITION_MAP.get(pos.get("code"))}
                            for pos in (p.get("positions") or [])
                        ],
                        "foot": _foot(p),
                        "current_ability": p.get("current_ability"),
                        "potential_ability": p.get("potential_ability"),
                        "value": p.get("value"),
                        "contract": p.get("contract"),
                        "history": p.get("history"),
                        "club": club_name,
                        "club_id": p.get("club_id"),
                        "league": league.get("name"),
                        "league_tier": league.get("tier"),
                        "country": cc,
                        # TM enrichment (filled by the join)
                        "height": None,
                        "tm_club_id": None,
                        "tm_club_name": None,
                        "nationality": None,
                    })
        logger.info("%s: %d players", cc, sum(1 for p in players if p["country"] == cc))
    return players


def load_tm_player_index() -> dict[str, dict]:
    """Index TM player id -> height/club/nationality from the resume cache rosters."""
    index: dict[str, dict] = {}
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT url, status, payload FROM requests "
            "WHERE url LIKE '%/clubs/%/players' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cannot read resume cache: %s", exc)
        return index
    import re
    for url, _status, payload in rows:
        m = re.search(r"/clubs/(\d+)/players", url)
        club_id = m.group(1) if m else None
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        for p in data.get("players") or []:
            pid = p.get("id")
            if not pid:
                continue
            index[str(pid)] = {
                "height": p.get("height"),
                "foot": p.get("foot"),
                "position": p.get("position"),
                "nationality": (p.get("nationality") or [None])[0] if isinstance(p.get("nationality"), list) else p.get("nationality"),
                "tm_club_id": club_id,
                "tm_club_name": None,
            }
    logger.info("TM roster index: %d players (from resume cache)", len(index))
    return index


def _load_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def join_and_dump(players: list[dict], tm_index: dict[str, dict], out: Path) -> None:
    matched = 0
    for p in players:
        tm = tm_index.get(p["tm_id"]) if p["tm_id"] else None
        if tm:
            p.update(tm)
            matched += 1
    out.mkdir(parents=True, exist_ok=True)
    for cc in sorted({p["country"] for p in players}):
        cc_players = [p for p in players if p["country"] == cc]
        with open(out / f"players_{cc}.json", "w", encoding="utf-8") as f:
            json.dump(cc_players, f, ensure_ascii=False, indent=1)
        with_h = sum(1 for p in cc_players if p["height"])
        logger.info("%s: %d players, height for %d (%.0f%%)", cc, len(cc_players),
                    with_h, 100 * with_h / max(len(cc_players), 1))
    logger.info("total: %d players, height for %d (%.0f%%)", len(players), matched,
                100 * matched / max(len(players), 1))
    logger.info("output -> %s", out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load open-football-database merged with TM rosters.")
    parser.add_argument("--countries", default="gb,es", help="comma-separated country codes")
    parser.add_argument("--of-path", default=str(OF_DATABASE_PATH))
    parser.add_argument("--out", default=str(OF_OUTPUT))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    players = load_openfootball_players([c.strip() for c in args.countries.split(",")], Path(args.of_path))
    tm_index = load_tm_player_index()
    join_and_dump(players, tm_index, Path(args.out))


if __name__ == "__main__":
    main()