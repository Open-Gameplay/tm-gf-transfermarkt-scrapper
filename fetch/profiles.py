"""Player profiles: per-player spot fetches (outfitter, image, extra positions).

Endpoint:
    GET /players/{id}/profile

Failures are NOT skipped: failed players go to a pending queue and are retried
in later rounds after a cooldown (see FETCH_RETRY_ROUNDS / FETCH_RETRY_COOLDOWN),
so no player ends up without spot data just because TM was temporarily blocking.
"""

from __future__ import annotations

import logging
import time

from client import Client
from config import FETCH_RETRY_COOLDOWN, FETCH_RETRY_ROUNDS

logger = logging.getLogger("scraper.fetch.profiles")


def player_profiles(
    player_ids: list[str],
    client: Client | None = None,
    *,
    rounds: int | None = None,
    cooldown: float | None = None,
) -> dict[str, dict]:
    """Fetch profiles for all player ids, retrying failures across rounds.

    Returns {player_id: profile} for every successfully fetched player.
    """
    client = client or _default_client()
    rounds = rounds if rounds is not None else FETCH_RETRY_ROUNDS
    cooldown = cooldown if cooldown is not None else FETCH_RETRY_COOLDOWN

    out: dict[str, dict] = {}
    pending = list(player_ids)
    for round_no in range(1, rounds + 1):
        still: list[str] = []
        for i, pid in enumerate(pending, 1):
            try:
                out[pid] = client.api(f"players/{pid}/profile")
            except Exception as exc:
                logger.warning("player profile %s failed (round %d/%d): %s",
                               pid, round_no, rounds, exc)
                still.append(pid)
            if i % 100 == 0 or i == len(pending):
                logger.info("profiles round %d/%d: %d/%d done",
                            round_no, rounds, i, len(pending))
        pending = still
        if not pending:
            break
        if round_no < rounds:
            logger.info("profiles: %d still failing, waiting %.0fs before round %d",
                        len(pending), cooldown, round_no + 1)
            time.sleep(cooldown)

    if pending:
        logger.warning("profiles: %d players still failed after %d rounds — rerun "
                       "to retry them (failures are not cached)", len(pending), rounds)
    return out


def _default_client() -> Client:
    from client import client as singleton
    return singleton