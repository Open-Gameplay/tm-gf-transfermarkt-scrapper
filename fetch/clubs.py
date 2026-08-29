"""Clubs: club profiles (colors, stadium, league, logo).

Endpoint:
    GET /clubs/{id}/profile
"""

from __future__ import annotations

import logging

from client import Client

logger = logging.getLogger("scraper.fetch.clubs")


def club_profiles(club_ids: list[str], client: Client | None = None) -> dict[str, dict]:
    """Fetch profiles for the given club ids.

    Returns {club_id: profile_dict}. Failed lookups are skipped and logged.
    """
    client = client or _default_client()
    profiles: dict[str, dict] = {}
    for cid in club_ids:
        try:
            profiles[cid] = client.api(f"clubs/{cid}/profile")
        except Exception as exc:
            logger.warning("club profile %s failed: %s", cid, exc)
    return profiles


def _default_client() -> Client:
    from client import client as singleton
    return singleton