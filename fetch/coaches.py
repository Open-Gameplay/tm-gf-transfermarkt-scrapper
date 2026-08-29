"""Coaches: profiles via the API + coach_id via direct TM /mitarbeiter/ parsing.

The transfermarkt-api has no coach_id in national team data, so we parse the
staff page directly (the only sanctioned direct-TM access besides CDN images).

Endpoints:
    GET /coaches/{id}/profile            (raw dict, snake_case — API convention)
    GET https://www.transfermarkt.com/-/mitarbeiter/verein/{team_id}  (direct)
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from client import Client

logger = logging.getLogger("scraper.fetch.coaches")

MITARBEITER_URL = "https://www.transfermarkt.com/-/mitarbeiter/verein/{team_id}"


def coach_profiles(coach_ids: list[str], client: Client | None = None) -> dict[str, dict]:
    """Fetch coach profiles. Returns {coach_id: profile_dict}."""
    client = client or _default_client()
    out: dict[str, dict] = {}
    for cid in coach_ids:
        try:
            out[cid] = client.api(f"coaches/{cid}/profile")
        except Exception as exc:
            logger.warning("coach profile %s failed: %s", cid, exc)
    return out


def coach_id_for_team(team_id: str, client: Client | None = None) -> str | None:
    """Extract the head coach id from the /mitarbeiter/ staff page.

    This is a direct TM request (rate-limited via the html bucket).
    """
    client = client or _default_client()
    resp = client.get_html(MITARBEITER_URL.format(team_id=team_id))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    coach_link = soup.select_one(
        "div.large-8.columns table.inline-table tr:first-child "
        "td:nth-child(2) a[href*='/trainer/']"
    ) or soup.select_one("a[href*='/profil/trainer/']")
    if not coach_link:
        return None

    coach_url = coach_link.get("href") or ""
    for marker in ("/trainer/", "/profil/trainer/"):
        if marker in coach_url:
            return coach_url.split(marker)[-1].split("/")[0]
    return None


def coach_ids_for_teams(team_ids: list[str], client: Client | None = None) -> dict[str, str | None]:
    """Map team_id -> coach_id via the staff pages."""
    client = client or _default_client()
    out: dict[str, str | None] = {}
    for tid in team_ids:
        try:
            out[tid] = coach_id_for_team(tid, client)
        except Exception as exc:
            logger.warning("coach id for team %s failed: %s", tid, exc)
            out[tid] = None
    return out


def _default_client() -> Client:
    from client import client as singleton
    return singleton