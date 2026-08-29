"""Player profiles: spot fetches for outfitter, image, extra positions.

Endpoint:
    GET /players/{id}/profile
"""

from __future__ import annotations

import logging

from client import Client

logger = logging.getLogger("scraper.fetch.profiles")


def player_profiles(player_ids: list[str], client: Client | None = None) -> dict[str, dict]:
    """Fetch profiles for the given player ids. Returns {player_id: profile}."""
    client = client or _default_client()
    out: dict[str, dict] = {}
    for pid in player_ids:
        try:
            out[pid] = client.api(f"players/{pid}/profile")
        except Exception as exc:
            logger.warning("player profile %s failed: %s", pid, exc)
    return out


def _default_client() -> Client:
    from client import client as singleton
    return singleton