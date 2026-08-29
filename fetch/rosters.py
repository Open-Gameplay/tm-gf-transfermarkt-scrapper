"""Rosters: main source of player data — one request per team.

Endpoints:
    GET /clubs/{id}/players
    GET /national-teams/{id}/players
"""

from __future__ import annotations

import logging

from client import Client

logger = logging.getLogger("scraper.fetch.rosters")


def club_roster(club_id: str, client: Client | None = None) -> list[dict]:
    """Fetch a club's squad from /clubs/{id}/players."""
    client = client or _default_client()
    data = client.api(f"clubs/{club_id}/players")
    return data.get("players") or []


def club_rosters(club_ids: list[str], client: Client | None = None) -> dict[str, list[dict]]:
    """Fetch squads for many clubs. Returns {club_id: [player, ...]}."""
    client = client or _default_client()
    out: dict[str, list[dict]] = {}
    for cid in club_ids:
        try:
            out[cid] = club_roster(cid, client)
        except Exception as exc:
            logger.warning("club roster %s failed: %s", cid, exc)
    return out


def national_team_roster(team_id: str, client: Client | None = None) -> list[dict]:
    """Fetch a national team squad (includes shirtNumber, added in the fork)."""
    client = client or _default_client()
    data = client.api(f"national-teams/{team_id}/players")
    return data.get("players") or []


def national_team_rosters(team_ids: list[str], client: Client | None = None) -> dict[str, list[dict]]:
    """Fetch squads for many national teams. Returns {team_id: [player, ...]}."""
    client = client or _default_client()
    out: dict[str, list[dict]] = {}
    for tid in team_ids:
        try:
            out[tid] = national_team_roster(tid, client)
        except Exception as exc:
            logger.warning("national team roster %s failed: %s", tid, exc)
    return out


def national_team_profiles(team_ids: list[str], client: Client | None = None) -> dict[str, dict]:
    """Fetch national team metadata (name, image/emblem).

    Endpoint: GET /national-teams/{id}/profile
    """
    client = client or _default_client()
    out: dict[str, dict] = {}
    for tid in team_ids:
        try:
            out[tid] = client.api(f"national-teams/{tid}/profile")
        except Exception as exc:
            logger.warning("national team profile %s failed: %s", tid, exc)
    return out


def _default_client() -> Client:
    from client import client as singleton
    return singleton