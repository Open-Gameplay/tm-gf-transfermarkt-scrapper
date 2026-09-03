"""Top-up data/full/clubs.json with clubs missing from the full crawl.

For every club in data/clubs.json that (a) belongs to a downloadable league
from tournament_structure.json and (b) is not yet present in
data/full/clubs.json, this script fetches exactly two endpoints:

    GET /clubs/{id}/profile   -> club, league and country metadata
    GET /clubs/{id}/players   -> squad

New countries / leagues / clubs are merged into the existing nested structure
(countries -> leagues -> clubs -> players) and flushed atomically every
--flush clubs (write to .tmp then os.replace), so an interrupted run never
corrupts the file. Successes are stored in the SQLite resume cache, failures
are not, so re-running simply continues where it stopped.

The image pass (club logos, league logos, country flags, player faces) runs
after the API pass using the CDN rate bucket; already-downloaded files are
skipped, making it resumable too. Use --images-only to backfill any missing
images across the whole file (e.g. after an interrupted image pass).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from client import Client
from config import FETCH_RETRY_COOLDOWN, FETCH_RETRY_ROUNDS, IMAGES_DIR, ROOT
from download_images import _extension

logger = logging.getLogger("scraper.topup")

DATA = ROOT / "data"
FULL_CLUBS_PATH = DATA / "full" / "clubs.json"
CLUBS_INDEX_PATH = DATA / "clubs.json"
STRUCTURE_PATH = DATA / "tournament_structure.json"

LEAGUE_LOGO_URL = "https://img.a.transfermarkt.technology/logo/header/{lid}.png"
FLAG_URL = "https://img.a.transfermarkt.technology/flagge/tiny/{cid}.png"
DEFAULT_FLUSH = 50


# --- loading -----------------------------------------------------------------


def downloadable_leagues() -> set[str]:
    """League ids from tournament_structure.json (tiers + youth)."""
    structure = json.loads(STRUCTURE_PATH.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for c_data in structure["countries"].values():
        for t in c_data.get("tiers", []):
            ids.add(t["league_id"])
        for y in c_data.get("youth", []):
            ids.add(y["league_id"])
    return ids


def club_index() -> dict[str, dict]:
    return json.loads(CLUBS_INDEX_PATH.read_text(encoding="utf-8"))


def load_full() -> list[dict]:
    return json.loads(FULL_CLUBS_PATH.read_text(encoding="utf-8"))


def existing_club_ids(full: list[dict]) -> set[str]:
    return {
        club["id"]
        for country in full
        for league in country.get("leagues", [])
        for club in league.get("clubs", [])
    }


# --- transform & merge -------------------------------------------------------


def club_entry(profile: dict, roster: dict) -> dict:
    """Old-format club record (snake_case) from API profile + roster."""
    return {
        "id": profile["id"],
        "name": profile.get("name"),
        "official_name": profile.get("officialName"),
        "logo_url": profile.get("image"),
        "colors": profile.get("colors") or [],
        "stadium": profile.get("stadiumName"),
        "stadium_seats": profile.get("stadiumSeats"),
        "founded": profile.get("foundedOn"),
        "squad": profile.get("squad") or {},
        "players": roster.get("players") or [],
    }


def merge_club(full: list[dict], profile: dict, roster: dict) -> str:
    """Insert the club under its authoritative league/country. Returns club id.

    Idempotent: a club already present (resume after a crash) is left untouched.
    New countries / leagues are appended to preserve the existing file order.
    """
    cid = profile["id"]
    league = profile.get("league") or {}
    league_id = league.get("id")
    country_id = league.get("countryId")
    if not league_id or not country_id:
        raise ValueError(f"club {cid}: no league/country in profile ({league})")

    country = next((c for c in full if c["id"] == country_id), None)
    if country is None:
        country = {
            "id": country_id,
            "name": league.get("countryName"),
            "flag_url": FLAG_URL.format(cid=country_id),
            "leagues": [],
        }
        full.append(country)

    lg = next((l for l in country["leagues"] if l["id"] == league_id), None)
    if lg is None:
        lg = {
            "id": league_id,
            "logo_url": LEAGUE_LOGO_URL.format(lid=league_id.lower()),
            "country_id": country_id,
            "name": league.get("name"),
            "tier": league.get("tier"),
            "clubs": [],
        }
        country["leagues"].append(lg)

    if not any(c["id"] == cid for c in lg["clubs"]):
        lg["clubs"].append(club_entry(profile, roster))
    return cid


def write_atomic(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --- fetch loop --------------------------------------------------------------


def fetch_pending(
    client: Client,
    full: list[dict],
    pending: list[str],
    limit: int,
    flush: int,
) -> tuple[dict[str, tuple[dict, dict]], list[str]]:
    """Fetch profile+roster for each pending club, retrying failures across rounds.

    Returns ({club_id: (profile, roster)} fetched this run, still-failed ids).
    """
    todo = pending if not limit else pending[:limit]
    added: dict[str, tuple[dict, dict]] = {}
    current = list(todo)
    for round_no in range(1, FETCH_RETRY_ROUNDS + 1):
        still: list[str] = []
        unflushed = 0
        for i, cid in enumerate(current, 1):
            try:
                profile = client.api(f"clubs/{cid}/profile")
                roster = client.api(f"clubs/{cid}/players")
                merge_club(full, profile, roster)
                added[cid] = (profile, roster)
                unflushed += 1
            except Exception as exc:
                logger.warning("club %s failed (round %d/%d): %s",
                               cid, round_no, FETCH_RETRY_ROUNDS, exc)
                still.append(cid)
            if unflushed >= flush:
                write_atomic(FULL_CLUBS_PATH, full)
                unflushed = 0
            if i % 100 == 0 or i == len(current):
                logger.info("topup round %d/%d: %d/%d clubs done",
                            round_no, FETCH_RETRY_ROUNDS, i, len(current))
        if unflushed:
            write_atomic(FULL_CLUBS_PATH, full)
        current = still
        if not current:
            break
        if round_no < FETCH_RETRY_ROUNDS:
            logger.info("%d clubs still failing, waiting %.0fs before round %d",
                        len(current), FETCH_RETRY_COOLDOWN, round_no + 1)
            time.sleep(FETCH_RETRY_COOLDOWN)
    return added, current


# --- images ------------------------------------------------------------------


def _mirror(url: str) -> str:
    """Same object on the akamaized host — img.a is flaky (random 504/502)."""
    if url.startswith("https://img.a.transfermarkt.technology/"):
        return "https://tmssl.akamaized.net/" + url.split("img.a.transfermarkt.technology/", 1)[1]
    return url


def _try_one(client: Client, url: str, dest: Path) -> bool:
    """Single-shot download: max_retries=1 so a bad URL costs one request and
    never opens the circuit or escalates backoff. Failures are skipped and
    retried on a later --images-only run."""
    try:
        resp = client.get_binary(url, timeout=60, max_retries=1)
        resp.raise_for_status()
        if resp.content and len(resp.content) > 100:
            dest.write_bytes(resp.content)
            return True
    except Exception as exc:
        logger.warning("image %s failed: %s", url, exc)
    return False


def _try_download(client: Client, url: str, dest: Path, *, try_big: bool = False) -> int:
    """Resumable, never-raising image download. Tries big -> medium -> mirror."""
    if not url or "default" in url.lower():
        return 0
    if dest.exists() and dest.stat().st_size > 0:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    candidates: list[str] = []
    if try_big and "/portrait/medium/" in url:
        candidates.append(url.replace("/portrait/medium/", "/portrait/big/"))
    candidates.append(url)
    for u in candidates:
        if _try_one(client, u, dest):
            return 1
        m = _mirror(u)
        if m != u and _try_one(client, m, dest):
            return 1
    return 0


def _league_dest(league_id: str) -> Path:
    return IMAGES_DIR / "leagues" / f"{league_id}.png"


def _flag_dest(country_id: str) -> Path:
    return IMAGES_DIR / "flags" / f"{country_id}.png"


def images_only(client: Client) -> dict[str, int]:
    """Backfill any missing images across the whole file (resumable, idempotent).

    Scans the full nested structure and downloads whatever is missing; existing
    files are skipped. This is the single catch-up path used both by --images-only
    and the default image phase, so interrupted runs lose nothing.
    """
    full = load_full()
    n_logos = n_faces = n_leagues = n_flags = 0
    seen_faces = 0
    for country in full:
        cid = country["id"]
        n_flags += _try_download(client, FLAG_URL.format(cid=cid), _flag_dest(cid))
        for league in country.get("leagues", []):
            lid = league["id"]
            n_leagues += _try_download(client, LEAGUE_LOGO_URL.format(lid=lid.lower()),
                                       _league_dest(lid))
            for club in league.get("clubs", []):
                logo = club.get("logo_url")
                if logo:
                    n_logos += _try_download(client, logo,
                                             IMAGES_DIR / "logos" / f"{club['id']}{_extension(logo)}")
                for p in club.get("players", []):
                    url, pid = p.get("imageUrl"), p.get("id")
                    if url and pid:
                        seen_faces += 1
                        n_faces += _try_download(client, url,
                                                 IMAGES_DIR / "faces" / f"{pid}.jpg", try_big=True)
                        if seen_faces % 2000 == 0:
                            logger.info(
                                "images: %d faces scanned, so far logos=%d faces=%d leagues=%d flags=%d",
                                seen_faces, n_logos, n_faces, n_leagues, n_flags,
                            )
    stats = {"logos": n_logos, "faces": n_faces, "league_logos": n_leagues, "flags": n_flags}
    logger.info("images pass done: %s", stats)
    return stats


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Top-up data/full/clubs.json")
    parser.add_argument("--limit", type=int, default=0, help="max clubs to fetch (0 = all)")
    parser.add_argument("--flush", type=int, default=DEFAULT_FLUSH,
                        help="flush the file every N merged clubs")
    parser.add_argument("--no-images", action="store_true", help="skip the image pass")
    parser.add_argument("--images-only", action="store_true",
                        help="only backfill images from the current file (no API calls)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    from client import client as api_client

    if args.images_only:
        images_only(api_client)
        return 0

    dl = downloadable_leagues()
    clubs = club_index()
    full = load_full()
    existing = existing_club_ids(full)
    pending = [
        cid for cid, info in clubs.items()
        if cid not in existing and any(lid in dl for lid in info.get("leagues", []))
    ]
    logger.info("downloadable leagues: %d, pending clubs: %d", len(dl), len(pending))

    try:
        added, failed = fetch_pending(api_client, full, pending, args.limit, args.flush)
    except KeyboardInterrupt:
        write_atomic(FULL_CLUBS_PATH, full)
        logger.info("interrupted — file flushed, re-run to continue (cache has the rest)")
        return 130

    logger.info("fetch done: %d clubs added, %d failed", len(added), len(failed))
    if failed:
        logger.warning("failed clubs: %s (rerun to retry — failures are not cached)",
                       ", ".join(failed[:20]))

    if not args.no_images:
        images_only(api_client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
