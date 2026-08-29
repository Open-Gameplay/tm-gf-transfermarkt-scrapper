"""Market values: spot fetches of value history (needed for max_market_value).

Primary source is the local API (GET /players/{id}/market_value), but TM
currently blocks that endpoint for the API's plain-requests/static-UA transport
(403). Our curl_cffi client fetches the underlying chart API directly, so on an
API 403/5xx we fall back to the direct TM ceapi JSON — the same justification as
the /mitarbeiter/ direct access in fetch/coaches.py.
"""

from __future__ import annotations

import logging
import re

from client import Client

logger = logging.getLogger("scraper.fetch.market_values")

# TM internal chart API: https://www.transfermarkt.com/ceapi/marketValueDevelopment/graph/{player_id}
CEAPI_URL = "https://www.transfermarkt.com/ceapi/marketValueDevelopment/graph/{player_id}"
WAPPEN_CLUB_RE = re.compile(r"/wappen/(?:profil|big)/(\d+)\.")


def player_market_values(player_ids: list[str], client: Client | None = None) -> dict[str, dict]:
    """Fetch market values + history for the given player ids.

    Returns {player_id: {updatedAt, id, marketValue, marketValueHistory, ranking}}.
    Falls back to direct TM ceapi JSON when the API endpoint is blocked (403/5xx).
    """
    client = client or _default_client()
    out: dict[str, dict] = {}
    for pid in player_ids:
        try:
            out[pid] = client.api(f"players/{pid}/market_value")
        except Exception as exc:
            logger.warning("market value %s via API failed (%s); trying direct TM ceapi", pid, exc)
            try:
                out[pid] = _fetch_direct(pid, client)
            except Exception as exc2:
                logger.warning("market value %s direct TM fetch failed: %s", pid, exc2)
    return out


def _fetch_direct(player_id: str, client: Client) -> dict:
    """Fetch market value history directly from the TM chart API (bypasses the
    API layer whose static-UA transport TM currently 403s)."""
    url = CEAPI_URL.format(player_id=player_id)
    resp = client.get_html(url)
    resp.raise_for_status()
    data = resp.json()

    history = []
    for entry in data.get("list") or []:
        wappen = entry.get("wappen") or ""
        m = WAPPEN_CLUB_RE.search(wappen)
        history.append({
            "date": entry.get("datum_mw"),
            "age": _int_or_none(entry.get("age")),
            "clubId": m.group(1) if m else None,
            "clubName": entry.get("verein"),
            "marketValue": entry.get("y") if isinstance(entry.get("y"), (int, float)) else None,
        })

    return {
        "id": player_id,
        "marketValue": data.get("current") if isinstance(data.get("current"), (int, float)) else None,
        "marketValueHistory": history,
        "ranking": {},
    }


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _default_client() -> Client:
    from client import client as singleton
    return singleton