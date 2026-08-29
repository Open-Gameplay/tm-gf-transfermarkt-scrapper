"""Competitions: resolve Tier-1 league names to ids and list their clubs.

Endpoints:
    GET /competitions/search/{name}
    GET /competitions/{id}/clubs
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from client import Client

logger = logging.getLogger("scraper.fetch.competitions")


def search_competitions(names: list[str], client: Client | None = None) -> list[dict]:
    """Resolve league names to competition records (best match per name).

    Returns a list of raw API records: {id, name, country, clubs, ...}.
    """
    client = client or _default_client()
    out: list[dict] = []
    for name in names:
        try:
            data = client.api(f"competitions/search/{quote(name)}")
        except Exception as exc:
            logger.warning("competition search %r failed: %s", name, exc)
            continue
        results = data.get("results") or []
        match = next(
            (r for r in results if r.get("name", "").strip().lower() == name.strip().lower()),
            None,
        ) or (results[0] if results else None)
        if match:
            out.append(match)
        else:
            logger.warning("no competition found for %r", name)
    return out


def competition_clubs(competition_id: str, client: Client | None = None) -> list[dict]:
    """List clubs of a competition: [{id, name}, ...]."""
    client = client or _default_client()
    data = client.api(f"competitions/{competition_id}/clubs")
    return data.get("clubs") or []


def _default_client() -> Client:
    from client import client as singleton
    return singleton