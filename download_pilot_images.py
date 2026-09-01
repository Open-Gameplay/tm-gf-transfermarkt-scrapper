"""Step 5: Download ALL images.

Faces (big portrait), club logos, league logos, NT emblems, country flags.
Downloads for ALL cached data (not just pilot).

Usage:
    python download_pilot_images.py
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from client import Client
from config import CACHE_PATH, IMAGES_DIR

logger = logging.getLogger("download_pilot_images")


def _ext(url: str, default: str = ".png") -> str:
    path = urlsplit(url).path
    ext = Path(path).suffix.lower()
    return ext if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else default


def _is_default(url: str | None) -> bool:
    return bool(url) and "default" in url.lower()


def _dl(client: Client, url: str, dest: Path) -> bool:
    if not url or _is_default(url):
        return False
    if dest.exists() and dest.stat().st_size > 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = client.get_binary(url, timeout=60)
        resp.raise_for_status()
        if resp.content and len(resp.content) > 100:
            dest.write_bytes(resp.content)
            return True
    except Exception:
        pass
    return False


def _dl_big(client: Client, url: str, dest: Path) -> bool:
    if not url or _is_default(url):
        return False
    if dest.exists() and dest.stat().st_size > 0:
        return False
    big = url.replace("/portrait/medium/", "/portrait/big/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = client.get_binary(big, timeout=60)
        resp.raise_for_status()
        if resp.content and len(resp.content) > 100:
            dest.write_bytes(resp.content)
            return True
    except Exception:
        pass
    return _dl(client, url, dest)


def _get_all_club_ids() -> set[str]:
    club_ids = set()
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/competitions/%/clubs' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return club_ids
    for (payload,) in rows:
        try:
            data = json.loads(payload)
            for c in data.get("clubs") or []:
                club_ids.add(c.get("id"))
        except (json.JSONDecodeError, TypeError):
            continue
    return club_ids


def _get_all_league_ids() -> set[str]:
    league_ids = set()
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/competitions/%/clubs' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return league_ids
    for (payload,) in rows:
        try:
            data = json.loads(payload)
            lid = data.get("id")
            if lid:
                league_ids.add(lid)
        except (json.JSONDecodeError, TypeError):
            continue
    return league_ids


def download_faces(client: Client) -> int:
    all_clubs = _get_all_club_ids()
    n = 0
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT url, payload FROM requests WHERE url LIKE '%/clubs/%/players' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cache: %s", exc)
        return 0
    for url, payload in rows:
        m = re.search(r"/clubs/(\d+)/players", url)
        if not m or m.group(1) not in all_clubs:
            continue
        try:
            players = json.loads(payload).get("players") or []
        except (json.JSONDecodeError, TypeError):
            continue
        for p in players:
            img_url = p.get("imageUrl")
            pid = p.get("id")
            if not img_url or not pid:
                continue
            dest = IMAGES_DIR / "faces" / f"{pid}.jpg"
            n += _dl_big(client, img_url, dest)
    logger.info("faces: %d downloaded", n)
    return n


def download_club_logos(client: Client) -> int:
    all_clubs = _get_all_club_ids()
    n = 0
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/clubs/%/profile' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cache: %s", exc)
        return 0
    for (payload,) in rows:
        try:
            data = json.loads(payload)
            cid = data.get("id")
            if cid not in all_clubs:
                continue
            logo = data.get("image")
            if logo and cid:
                dest = IMAGES_DIR / "logos" / f"{cid}{_ext(logo)}"
                n += _dl(client, logo, dest)
        except (json.JSONDecodeError, TypeError):
            continue
    logger.info("club logos: %d downloaded", n)
    return n


def download_league_logos(client: Client) -> int:
    league_ids = _get_all_league_ids()
    n = 0
    for lid in league_ids:
        url = f"https://img.a.transfermarkt.technology/logo/header/{lid.lower()}.png"
        dest = IMAGES_DIR / "leagues" / f"{lid}.png"
        n += _dl(client, url, dest)
    logger.info("league logos: %d downloaded", n)
    return n


def download_nt_emblems(client: Client) -> int:
    n = 0
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/national-teams/%/profile' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cache: %s", exc)
        return 0
    for (payload,) in rows:
        try:
            data = json.loads(payload)
            emblem = data.get("image")
            tid = data.get("id")
            if emblem and tid:
                dest = IMAGES_DIR / "emblems" / f"{tid}{_ext(emblem)}"
                n += _dl(client, emblem, dest)
        except (json.JSONDecodeError, TypeError):
            continue
    logger.info("NT emblems: %d downloaded", n)
    return n


def download_flags(client: Client) -> int:
    n = 0
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/national-teams/%/profile' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error as exc:
        logger.error("cache: %s", exc)
        return 0
    country_ids = set()
    for (payload,) in rows:
        try:
            data = json.loads(payload)
            country_id = data.get("countryId") or data.get("country_id")
            if country_id:
                country_ids.add(str(country_id))
        except (json.JSONDecodeError, TypeError):
            continue
    for cid in country_ids:
        url = f"https://img.a.transfermarkt.technology/flagge/tiny/{cid}.png"
        dest = IMAGES_DIR / "flags" / f"{cid}.png"
        n += _dl(client, url, dest)
    logger.info("flags: %d downloaded", n)
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = Client()
    stats = {
        "faces": download_faces(client),
        "club_logos": download_club_logos(client),
        "league_logos": download_league_logos(client),
        "nt_emblems": download_nt_emblems(client),
        "flags": download_flags(client),
    }
    logger.info("total: %s", stats)


if __name__ == "__main__":
    main()
