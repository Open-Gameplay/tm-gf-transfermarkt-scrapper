"""Fetch player extras: stats, injuries, achievements via the API.

These endpoints (/players/{id}/stats, /injuries, /achievements) go through the
local API which scrapes TM. They MAY be blocked similarly to player profiles
(~15-20 requests → ban). The script uses very conservative settings:
  - weight=2 per request (each API call = 1 live TM request)
  - Monitors 403 ratio and stops if sustained block detected

NOT cached by default — use --cache to enable. Player extras are optional enrichment.

Usage:
    python fetch_player_extras.py                    # stats for first 100 players (test)
    python fetch_player_extras.py --all              # ALL players (slow, ~107k × 3 = 321k reqs)
    python fetch_player_extras.py --limit 500        # first 500 players
    python fetch_player_extras.py --type stats       # only stats
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from collections import deque

from client import Client
from config import CACHE_PATH

logger = logging.getLogger("scraper.fetch_player_extras")

EXTRA_TYPES = ("stats", "injuries", "achievements")

BLOCK_STOP_THRESHOLD = 10  # consecutive 403s before stopping
BLOCK_STOP_WINDOW = 30     # sliding window for 403 ratio check
BLOCK_STOP_RATIO = 0.7     # if 70%+ are 403 in window, stop


def collect_player_ids(client: Client) -> list[str]:
    """Extract all unique player IDs from cached club rosters."""
    player_ids: list[str] = []
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests "
            "WHERE url LIKE '%/clubs/%/players' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cannot read resume cache: %s", exc)
        return player_ids

    for (payload,) in rows:
        try:
            players = json.loads(payload).get("players") or []
        except (json.JSONDecodeError, TypeError):
            continue
        for p in players:
            pid = p.get("id")
            if pid and pid not in player_ids:
                player_ids.append(pid)

    logger.info("collected %d unique player IDs from rosters", len(player_ids))
    return player_ids


def _cached_has(client: Client, url_key: str) -> bool:
    cache = client._cache
    if not cache:
        return False
    hit = cache.get(url_key)
    if not hit:
        return False
    try:
        data = json.loads(hit[1])
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch player extras (stats/injuries/achievements) with conservative rate limiting."
    )
    parser.add_argument("--all", action="store_true",
                        help="fetch for ALL players in the cache (~107k)")
    parser.add_argument("--limit", type=int, default=100,
                        help="number of players to process (default: 100, ignored with --all)")
    parser.add_argument("--type", choices=EXTRA_TYPES, default=None,
                        help="only fetch this extra type (default: all)")
    parser.add_argument("--rps", type=float, default=0.5,
                        help="conservative rate limit (default: 0.5 rps)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    client = Client(html_rps=args.rps, html_burst=1)
    player_ids = collect_player_ids(client)
    if not args.all:
        player_ids = player_ids[: args.limit]
    logger.info("processing %d players at %.1f rps", len(player_ids), args.rps)

    types_to_fetch = [args.type] if args.type else list(EXTRA_TYPES)
    total = len(player_ids)
    stats = {t: {"ok": 0, "skip": 0, "fail": 0} for t in types_to_fetch}
    outcomes = deque(maxlen=BLOCK_STOP_WINDOW)
    consecutive_403 = 0

    for i, pid in enumerate(player_ids, 1):
        for t in types_to_fetch:
            endpoint = f"players/{pid}/{t}"
            url_key = f"{client.api_base}/{endpoint}"

            if _cached_has(client, url_key):
                stats[t]["skip"] += 1
                outcomes.append(True)
                continue

            try:
                client.api(endpoint, use_cache=False)
                stats[t]["ok"] += 1
                outcomes.append(True)
                consecutive_403 = 0
            except Exception as exc:
                msg = str(exc)
                if "403" in msg:
                    stats[t]["fail"] += 1
                    outcomes.append(False)
                    consecutive_403 += 1
                else:
                    stats[t]["fail"] += 1
                    outcomes.append(True)  # non-block error
                    consecutive_403 = 0

        # Block detection
        if len(outcomes) >= BLOCK_STOP_WINDOW:
            ratio = outcomes.count(False) / len(outcomes)
            if ratio >= BLOCK_STOP_RATIO:
                logger.error("SUSTAINED BLOCK DETECTED (%.0f%% 403 in last %d requests) — stopping",
                             ratio * 100, BLOCK_STOP_WINDOW)
                break
        if consecutive_403 >= BLOCK_STOP_THRESHOLD:
            logger.error("CONSECUTIVE 403 STREAK (%d) — stopping", consecutive_403)
            break

        if i % 50 == 0 or i == total:
            summary = " | ".join(f"{t}: ok={stats[t]['ok']} skip={stats[t]['skip']} fail={stats[t]['fail']}"
                                  for t in types_to_fetch)
            logger.info("progress %d/%d | %s", i, total, summary)

    logger.info("DONE: %d players | %s",
                total,
                " | ".join(f"{t}: ok={stats[t]['ok']} skip={stats[t]['skip']} fail={stats[t]['fail']}"
                           for t in types_to_fetch))


if __name__ == "__main__":
    main()
