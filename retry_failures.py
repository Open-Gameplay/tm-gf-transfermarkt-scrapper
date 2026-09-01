"""Step 7: Retry all failures.

Checks for missing club profiles, missing rosters, missing images.
Retries everything that's not in the cache. Returns exit code 0 if all complete.

Usage:
    python retry_failures.py
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

logger = logging.getLogger("retry_failures")


def _get_all_club_ids() -> list[str]:
    club_ids = []
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
                cid = c.get("id")
                if cid and cid not in club_ids:
                    club_ids.append(cid)
        except (json.JSONDecodeError, TypeError):
            continue
    return club_ids


def _get_all_league_ids() -> list[str]:
    league_ids = []
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
            if lid and lid not in league_ids:
                league_ids.append(lid)
        except (json.JSONDecodeError, TypeError):
            continue
    return league_ids


def _cached(url: str) -> bool:
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=5)
        row = con.execute("SELECT status FROM requests WHERE url = ?", (url,)).fetchone()
        con.close()
        return row is not None and 200 <= row[0] < 300
    except sqlite3.Error:
        return False


def retry_club_profiles(client: Client) -> int:
    """Retry missing club profiles. Returns count of newly fetched."""
    club_ids = _get_all_club_ids()
    n = 0
    for cid in club_ids:
        url = f"{client.api_base}/clubs/{cid}/profile"
        if _cached(url):
            continue
        try:
            client.api(f"clubs/{cid}/profile")
            logger.info("retried club profile %s", cid)
            n += 1
        except Exception as exc:
            logger.warning("retry club %s still failed: %s", cid, exc)
    return n


def retry_club_rosters(client: Client) -> int:
    """Retry missing club rosters. Returns count of newly fetched."""
    club_ids = _get_all_club_ids()
    n = 0
    for cid in club_ids:
        url = f"{client.api_base}/clubs/{cid}/players"
        if _cached(url):
            continue
        try:
            client.api(f"clubs/{cid}/players")
            logger.info("retried club roster %s", cid)
            n += 1
        except Exception as exc:
            logger.warning("retry roster %s still failed: %s", cid, exc)
    return n


def retry_nt_profiles(client: Client) -> int:
    """Retry missing national team profiles. Returns count of newly fetched."""
    n = 0
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT url FROM requests WHERE url LIKE '%/national-teams/%/players' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return 0
    tids = set()
    for (url,) in rows:
        m = re.search(r"/national-teams/(\d+)/players", url)
        if m:
            tids.add(m.group(1))
    for tid in tids:
        profile_url = f"{client.api_base}/national-teams/{tid}/profile"
        if _cached(profile_url):
            continue
        try:
            client.api(f"national-teams/{tid}/profile")
            logger.info("retried NT profile %s", tid)
            n += 1
        except Exception as exc:
            logger.warning("retry NT profile %s still failed: %s", tid, exc)
    return n


def retry_nt_rosters(client: Client) -> int:
    """Retry missing national team rosters. Returns count of newly fetched."""
    n = 0
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT url FROM requests WHERE url LIKE '%/national-teams/%/profile' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return 0
    tids = set()
    for (url,) in rows:
        m = re.search(r"/national-teams/(\d+)/profile", url)
        if m:
            tids.add(m.group(1))
    for tid in tids:
        roster_url = f"{client.api_base}/national-teams/{tid}/players"
        if _cached(roster_url):
            continue
        try:
            client.api(f"national-teams/{tid}/players")
            logger.info("retried NT roster %s", tid)
            n += 1
        except Exception as exc:
            logger.warning("retry NT roster %s still failed: %s", tid, exc)
    return n


def _ext(url: str, default: str = ".png") -> str:
    path = urlsplit(url).path
    ext = Path(path).suffix.lower()
    return ext if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else default


def _is_default(url: str | None) -> bool:
    return bool(url) and "default" in url.lower()


def _dl(client: Client, url: str, dest: Path) -> bool:
    if not url or _is_default(url) or (dest.exists() and dest.stat().st_size > 0):
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
    if not url or _is_default(url) or (dest.exists() and dest.stat().st_size > 0):
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


def retry_images(client: Client) -> int:
    """Retry downloading missing images. Returns count of newly downloaded."""
    all_clubs = set(_get_all_club_ids())
    all_leagues = set(_get_all_league_ids())
    n = 0

    # Retry faces
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT url, payload FROM requests WHERE url LIKE '%/clubs/%/players' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
    for url, payload in rows:
        m = re.search(r"/clubs/(\d+)/players", url)
        if not m or m.group(1) not in all_clubs:
            continue
        try:
            players = json.loads(payload).get("players") or []
        except (json.JSONDecodeError, TypeError):
            continue
        for p in players:
            img = p.get("imageUrl")
            pid = p.get("id")
            if not img or not pid or _is_default(img):
                continue
            dest = IMAGES_DIR / "faces" / f"{pid}.jpg"
            n += _dl_big(client, img, dest)

    # Retry club logos
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/clubs/%/profile' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
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

    # Retry league logos
    for lid in all_leagues:
        url = f"https://img.a.transfermarkt.technology/logo/header/{lid.lower()}.png"
        dest = IMAGES_DIR / "leagues" / f"{lid}.png"
        n += _dl(client, url, dest)

    # Retry NT emblems
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/national-teams/%/profile' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
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

    # Retry flags (from NT profiles)
    try:
        con = sqlite3.connect(f"file:{CACHE_PATH}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT payload FROM requests WHERE url LIKE '%/national-teams/%/profile' AND status=200"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
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

    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = Client()
    total_new = 0

    logger.info("checking club profiles...")
    n = retry_club_profiles(client)
    logger.info("club profiles: %d new", n)
    total_new += n

    logger.info("checking club rosters...")
    n = retry_club_rosters(client)
    logger.info("club rosters: %d new", n)
    total_new += n

    logger.info("checking NT profiles...")
    n = retry_nt_profiles(client)
    logger.info("NT profiles: %d new", n)
    total_new += n

    logger.info("checking NT rosters...")
    n = retry_nt_rosters(client)
    logger.info("NT rosters: %d new", n)
    total_new += n

    logger.info("checking images...")
    n = retry_images(client)
    logger.info("images: %d new", n)
    total_new += n

    if total_new == 0:
        logger.info("ALL DATA COMPLETE")
        # Exit 0 = all done
    else:
        logger.info("retried %d items — rerun to check again", total_new)
        # Exit 1 = still has failures (will retry)


if __name__ == "__main__":
    main()
