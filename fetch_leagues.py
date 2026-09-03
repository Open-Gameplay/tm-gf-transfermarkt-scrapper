"""Step 1: Discover leagues via API search and fetch their club lists.

Searches for competitions by country name, filters to real leagues
(excludes cups/international), deduplicates, and fetches club lists.

Usage:
    python fetch_leagues.py                          # all countries
    python fetch_leagues.py --countries Russia England Spain
    python fetch_leagues.py --countries Russia --max-pages 5
    python fetch_leagues.py --min-clubs 10
    python fetch_leagues.py --output data/leagues.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path

from client import Client

logger = logging.getLogger("fetch_leagues")

# ISO 3166-1 country names for TM search.
# TM search is fuzzy: "Russia" finds RU1, RU2, R3D1, etc.
COUNTRIES: list[str] = [
    "Russia", "England", "Spain", "Germany", "France", "Italy",
    "Brazil", "Argentina", "Portugal", "Netherlands", "Belgium",
    "Turkey", "Ukraine", "Greece", "Denmark", "Norway", "Sweden",
    "Switzerland", "Austria", "Czech Republic", "Poland", "Romania",
    "Croatia", "Serbia", "Bulgaria", "Hungary", "Slovakia", "Slovenia",
    "Chile", "Colombia", "Mexico", "United States", "Japan",
    "South Korea", "China", "India", "Australia", "Saudi Arabia",
    "United Arab Emirates", "Egypt", "Morocco", "Tunisia", "Nigeria",
    "South Africa", "Algeria", "Ghana", "Cameroon", "Senegal",
    "Ivory Coast", "Paraguay", "Uruguay", "Ecuador", "Peru",
    "Bolivia", "Venezuela", "Costa Rica", "Panama", "Honduras",
    "Guatemala", "El Salvador", "Nicaragua", "Cuba", "Jamaica",
    "Trinidad and Tobago", "Haiti", "Dominican Republic",
    "Czechia", "Iceland", "Finland", "Ireland", "Northern Ireland",
    "Scotland", "Wales", "Israel", "Lebanon", "Jordan",
    "Iraq", "Iran", "Kuwait", "Qatar", "Bahrain", "Oman", "Yemen",
    "Syria", "Palestine", "Libya", "Sudan", "Ethiopia", "Kenya",
    "Uganda", "Tanzania", "Mozambique", "Angola", "Zambia",
    "Zimbabwe", "Botswana", "Namibia", "Madagascar", "Mauritius",
    "Réunion", "New Zealand", "Indonesia", "Thailand", "Vietnam",
    "Philippines", "Malaysia", "Singapore", "Myanmar", "Cambodia",
    "Taiwan", "Hong Kong", "Kazakhstan", "Uzbekistan", "Georgia",
    "Armenia", "Azerbaijan", "Moldova", "Belarus", "Lithuania",
    "Latvia", "Estonia", "Cyprus", "Malta", "Luxembourg",
    "Monaco", "Andorra", "San Marino", "Liechtenstein", "Faroe Islands",
    "Gibraltar", "Kosovo", "North Macedonia", "Albania", "Montenegro",
    "Bosnia and Herzegovina",
]

# Patterns for non-league competitions (cups, international, super cups, etc.)
# Matched against the competition name (case-insensitive).
# Uses substring match for unambiguous words (cup, copa, pokal, etc.)
# and word-boundary match for ambiguous ones (champions, afc, caf).
_CUP_KEYWORDS: list[tuple[re.Pattern, bool]] = [
    # (pattern, use_word_boundary)
    (re.compile(r"cup", re.IGNORECASE), False),
    (re.compile(r"copa", re.IGNORECASE), False),
    (re.compile(r"coupe", re.IGNORECASE), False),
    (re.compile(r"pokal", re.IGNORECASE), False),
    (re.compile(r"super\s*cup", re.IGNORECASE), False),
    (re.compile(r"supercoppa", re.IGNORECASE), False),
    (re.compile(r"supercopa", re.IGNORECASE), False),
    (re.compile(r"shield", re.IGNORECASE), False),
    (re.compile(r"trophy", re.IGNORECASE), False),
    (re.compile(r"league\s*cup", re.IGNORECASE), False),
    (re.compile(r"community\s*shield", re.IGNORECASE), False),
    (re.compile(r"fa\s*cup", re.IGNORECASE), False),
    (re.compile(r"intertoto", re.IGNORECASE), False),
    (re.compile(r"\bchampions\b", re.IGNORECASE), True),
    (re.compile(r"ueling", re.IGNORECASE), False),
    (re.compile(r"europa\s*league", re.IGNORECASE), False),
    (re.compile(r"europa\s*conference", re.IGNORECASE), False),
    (re.compile(r"conference\s*league", re.IGNORECASE), False),
    (re.compile(r"copa\s*libertadores", re.IGNORECASE), False),
    (re.compile(r"copa\s*sudamericana", re.IGNORECASE), False),
    (re.compile(r"reco\s*sudamericana", re.IGNORECASE), False),
    (re.compile(r"\bafc\b", re.IGNORECASE), True),
    (re.compile(r"\bcaf\b", re.IGNORECASE), True),
    (re.compile(r"world\s*cup", re.IGNORECASE), False),
    (re.compile(r"qualification", re.IGNORECASE), False),
    (re.compile(r"play-?off", re.IGNORECASE), False),
    (re.compile(r"relegation", re.IGNORECASE), False),
    (re.compile(r"supercup", re.IGNORECASE), False),
]

# Minimum clubs to consider a competition a real league.
_MIN_CLUBS = 8

# Country search returns duplicates across pages; cap per country.
_MAX_PAGES_DEFAULT = 15


def _is_league(comp: dict) -> bool:
    """Return True if the competition looks like a real league (not a cup)."""
    name = comp.get("name", "")
    clubs = comp.get("clubs", 0)
    if clubs < _MIN_CLUBS:
        return False
    for pat, _use_boundary in _CUP_KEYWORDS:
        if pat.search(name):
            return False
    return True


def discover_leagues(
    client: Client,
    countries: list[str] | None = None,
    max_pages: int = _MAX_PAGES_DEFAULT,
) -> dict[str, dict]:
    """Search API for leagues in each country, deduplicate by ID.

    Returns {competition_id: {id, name, country, clubs, players, ...}}.
    """
    countries = countries or COUNTRIES
    seen: dict[str, dict] = {}
    for country in countries:
        try:
            first = client.api(f"competitions/search/{country}")
        except Exception as exc:
            logger.warning("search %s failed: %s", country, exc)
            continue
        pages = min(first.get("lastPageNumber", 1), max_pages)
        _collect_results(first, seen)
        for page in range(2, pages + 1):
            try:
                data = client.api(f"competitions/search/{country}?page={page}")
                _collect_results(data, seen)
            except Exception as exc:
                logger.warning("search %s page %d failed: %s", country, page, exc)
        logger.info("country %s: %d unique so far", country, len(seen))
    return seen


def _collect_results(data: dict, seen: dict[str, dict]) -> None:
    for r in data.get("results") or []:
        cid = r.get("id", "")
        if cid and cid not in seen:
            seen[cid] = r


def filter_leagues(
    all_comps: dict[str, dict],
    min_clubs: int = _MIN_CLUBS,
) -> dict[str, dict]:
    """Keep only real leagues (not cups, not too small)."""
    filtered = {}
    for cid, comp in all_comps.items():
        if comp.get("clubs", 0) < min_clubs:
            continue
        if not _is_league(comp):
            continue
        filtered[cid] = comp
    return filtered


def fetch_league_clubs(
    client: Client,
    league_ids: list[str],
) -> dict[str, list[dict]]:
    """Fetch /competitions/{id}/clubs for each league."""
    result: dict[str, list[dict]] = {}
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
    parser = argparse.ArgumentParser(description="Discover leagues and fetch club lists.")
    parser.add_argument("--countries", nargs="*", default=None,
                        help="country names to search (default: all)")
    parser.add_argument("--max-pages", type=int, default=_MAX_PAGES_DEFAULT,
                        help="max search pages per country (default: %(default)s)")
    parser.add_argument("--min-clubs", type=int, default=_MIN_CLUBS,
                        help="min clubs to keep a league (default: %(default)s)")
    parser.add_argument("--output", type=str, default="data/leagues.json",
                        help="output JSON path (default: %(default)s)")
    parser.add_argument("--clubs", action="store_true",
                        help="also fetch club lists (slower: 1 API call per league)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client()

    # Phase 1: discover leagues
    logger.info("phase 1: discovering leagues ...")
    all_comps = discover_leagues(client, args.countries, args.max_pages)
    logger.info("discovered %d unique competitions", len(all_comps))

    # Phase 2: filter to real leagues
    leagues = filter_leagues(all_comps, args.min_clubs)
    logger.info("filtered to %d leagues (min_clubs=%d)", len(leagues), args.min_clubs)

    # Phase 3: fetch clubs (optional)
    club_data: dict[str, list[dict]] = {}
    if args.clubs:
        logger.info("phase 3: fetching clubs for %d leagues ...", len(leagues))
        club_data = fetch_league_clubs(client, list(leagues.keys()))
    else:
        logger.info("phase 3: skipped (use --clubs to fetch club lists)")

    # Save output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "meta": {
            "total_competitions": len(all_comps),
            "total_leagues": len(leagues),
            "total_clubs_in_leagues": sum(c.get("clubs", 0) for c in leagues.values()),
            "total_players_in_leagues": sum(c.get("players", 0) for c in leagues.values()),
        },
        "leagues": leagues,
    }
    if club_data:
        output["clubs"] = club_data
        output["meta"]["total_clubs_fetched"] = sum(len(v) for v in club_data.values())

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info("saved to %s", out_path)

    # Summary
    total_clubs = sum(c.get("clubs", 0) for c in leagues.values())
    total_players = sum(c.get("players", 0) for c in leagues.values())
    logger.info(
        "done: %d leagues, ~%d clubs, ~%d players",
        len(leagues), total_clubs, total_players,
    )


if __name__ == "__main__":
    main()
