"""Download club logos, national team emblems and player faces from the TM CDN.

Requests go through client.get_binary() which uses the CDN rate bucket (probed
separately from HTML — the CDN is not throttled like www.transfermarkt.com).
Already-downloaded files are skipped, so the download is resumable/idempotent.

Output layout under data/images/:
    logos/<club_id>.<ext>      club crests
    emblems/<team_id>.<ext>    national team emblems
    faces/<player_id>.<ext>    player portraits
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from client import Client
from config import IMAGES_DIR

logger = logging.getLogger("scraper.download_images")


def _extension(url: str, default: str = ".png") -> str:
    path = urlsplit(url).path
    ext = Path(path).suffix.lower()
    return ext if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else default


def _is_default_image(url: str | None) -> bool:
    """True for TM placeholder images (players without a real photo)."""
    return bool(url) and "default" in url.lower()


def _download(client: Client, url: str, dest: Path) -> bool:
    if not url:
        return False
    if _is_default_image(url):
        return False
    if dest.exists() and dest.stat().st_size > 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = client.get_binary(url, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    logger.debug("downloaded %s -> %s", url, dest)
    return True


def download_club_logos(club_profiles: dict[str, dict], client: Client | None = None) -> int:
    client = client or _default_client()
    n = 0
    for cid, profile in club_profiles.items():
        url = profile.get("image")
        dest = IMAGES_DIR / "logos" / f"{cid}{_extension(url or 'x.png')}"
        n += _download(client, url, dest)
    return n


def download_nt_emblems(nt_profiles: dict[str, dict], client: Client | None = None) -> int:
    client = client or _default_client()
    n = 0
    for tid, profile in nt_profiles.items():
        url = profile.get("image")
        dest = IMAGES_DIR / "emblems" / f"{tid}{_extension(url or 'x.png')}"
        n += _download(client, url, dest)
    return n


def download_player_faces(player_profiles: dict[str, dict], client: Client | None = None) -> int:
    client = client or _default_client()
    n = 0
    for pid, profile in player_profiles.items():
        url = profile.get("imageUrl") or profile.get("image")
        dest = IMAGES_DIR / "faces" / f"{pid}{_extension(url or 'x.jpg')}"
        n += _download(client, url, dest)
    return n


def download_canon_images(
    club_profiles: dict[str, dict],
    nt_profiles: dict[str, dict],
    player_profiles: dict[str, dict],
    client: Client | None = None,
) -> dict[str, int]:
    """Download all images referenced by the canon records."""
    client = client or _default_client()
    stats = {
        "logos": download_club_logos(club_profiles, client),
        "emblems": download_nt_emblems(nt_profiles, client),
        "faces": download_player_faces(player_profiles, client),
    }
    logger.info("images downloaded: %s", stats)
    return stats


def download_faces_from_cache(client: Client | None = None) -> int:
    """Download every player face from the club-roster cache (imageUrl added by
    the API's /clubs/{id}/players). CDN bucket — no profile pages involved."""
    import json
    import sqlite3

    from config import CACHE_PATH
    client = client or _default_client()
    n = 0
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests "
            "WHERE url LIKE '%/clubs/%/players' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cannot read resume cache: %s", exc)
        return 0

    for (payload,) in rows:
        try:
            players = json.loads(payload).get("players") or []
        except (json.JSONDecodeError, TypeError):
            continue
        for p in players:
            url = p.get("imageUrl")
            pid = p.get("id")
            if not url or not pid or _is_default_image(url):
                continue
            dest = IMAGES_DIR / "faces" / f"{pid}.jpg"
            n += _download(client, url, dest)
    logger.info("faces downloaded from rosters: %d", n)
    return n


def _default_client() -> Client:
    from client import client as singleton
    return singleton