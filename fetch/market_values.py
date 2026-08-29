"""Market values: spot fetches of value history (needed for max_market_value).

Endpoint:
    GET /players/{id}/market_value
"""

from __future__ import annotations

import logging

from client import Client

logger = logging.getLogger("scraper.fetch.market_values")


def player_market_values(player_ids: list[str], client: Client | None = None) -> dict[str, dict]:
    """Fetch market values + history for the given player ids.

    Returns {player_id: {updatedAt, id, marketValue, marketValueHistory, ranking}}.
    """
    client = client or _default_client()
    out: dict[str, dict] = {}
    for pid in player_ids:
        try:
            out[pid] = client.api(f"players/{pid}/market_value")
        except Exception as exc:
            logger.warning("market value %s failed: %s", pid, exc)
    return out


def _default_client() -> Client:
    from client import client as singleton
    return singleton