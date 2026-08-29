"""Scraper configuration: API base, rate limits, Tier-1 coverage, output paths.

Every value can be overridden via a TM_* env var. The rate limits below are
the experimentally established safe values (see docs/wiki/конвейер.md, section
«Лимиты»). Do not raise them without re-running the probe scripts.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- Local transfermarkt-api -------------------------------------------------
API_BASE = os.getenv("TM_API_BASE", "http://127.0.0.1:8000").rstrip("/")

# --- Rate limits -------------------------------------------------------------
# HTML endpoints go through the local API: one API call == one live TM request.
# Established by probe on 2026-08-29 (see docs/wiki/конвейер.md, section «Лимиты»):
#   - short bursts are clean even at 75 rps (homepage), BUT
#   - sensitive pages (/profil/spieler/, /marktwertverlauf/) get volume-blocked
#     after ~15-24 sustained requests even at 8 rps (verified: once blocked, new
#     URLs return 403/0B and stay blocked for those URLs).
# => the sustainable HTML rate is ~1-2 rps; the adaptive rate halves on 403
#    bursts and slowly grows back, so the crawl self-regulates.
HTML_RPS = float(os.getenv("TM_HTML_RPS", "2.0"))
HTML_BURST = int(os.getenv("TM_HTML_BURST", "2"))

# CDN image hosts (img.a.transfermarkt.technology, ...) — probed separately,
# NOT throttled like www.transfermarkt.com (clean at 20 rps) -> safe = 15 rps.
CDN_RPS = float(os.getenv("TM_CDN_RPS", "15.0"))
CDN_BURST = int(os.getenv("TM_CDN_BURST", "6"))

# --- HTTP --------------------------------------------------------------------
HTTP_TIMEOUT = float(os.getenv("TM_HTTP_TIMEOUT", "30"))
HTTP_MAX_RETRIES = int(os.getenv("TM_HTTP_MAX_RETRIES", "3"))
CONCURRENCY = int(os.getenv("TM_CONCURRENCY", "2"))  # max in-flight requests

# Circuit breaker: after N consecutive failures pause all requests.
CIRCUIT_FAILS = int(os.getenv("TM_CIRCUIT_FAILS", "3"))
CIRCUIT_PAUSE = float(os.getenv("TM_CIRCUIT_PAUSE", "120"))

# Block detection (403 responses): the client tracks the recent 403 ratio per
# bucket over a sliding window and reacts by severity:
#   ratio >= BLOCK_RATIO_HIGH  -> sustained block: open the circuit (stop hammering)
#   BLOCK_RATIO_MID <= ratio < BLOCK_RATIO_HIGH -> clustered blocks: halve the rate
#   below -> scattered honeypots: do nothing special
BLOCK_WINDOW = int(os.getenv("TM_BLOCK_WINDOW", "20"))
BLOCK_RATIO_HIGH = float(os.getenv("TM_BLOCK_RATIO_HIGH", "0.7"))
BLOCK_RATIO_MID = float(os.getenv("TM_BLOCK_RATIO_MID", "0.4"))

# Retry queue for per-player fetches (profiles / market values): failed players
# are re-tried in later rounds after a cooldown, so no player is skipped just
# because TM was temporarily blocking at fetch time.
FETCH_RETRY_ROUNDS = int(os.getenv("TM_FETCH_RETRY_ROUNDS", "5"))
FETCH_RETRY_COOLDOWN = float(os.getenv("TM_FETCH_RETRY_COOLDOWN", "60"))  # seconds

# --- Adaptive rate (AIMD on rps) ---------------------------------------------
# If the session starts throttling, the client halves the bucket rate down to
# the floor, then slowly grows back to the configured max. The learned rate is
# persisted to RATES_PATH and reused on the next start (remembers constraints).
HTML_RPS_MIN = float(os.getenv("TM_HTML_RPS_MIN", "0.5"))
CDN_RPS_MIN = float(os.getenv("TM_CDN_RPS_MIN", "5.0"))
RATE_GROW_AFTER = int(os.getenv("TM_RATE_GROW_AFTER", "50"))    # successes per +grow
RATE_GROW_FACTOR = float(os.getenv("TM_RATE_GROW_FACTOR", "1.25"))
RATE_DROP_FACTOR = float(os.getenv("TM_RATE_DROP_FACTOR", "0.5"))
RATES_PATH = Path(os.getenv("TM_RATES_PATH", str(ROOT / "data" / "rates.json")))

# --- Resume cache ------------------------------------------------------------
CACHE_PATH = Path(os.getenv("TM_CACHE_PATH", str(ROOT / "tm_cache.sqlite3")))
CACHE_TTL = int(os.getenv("TM_CACHE_TTL", str(7 * 24 * 3600)))
CACHE_ENABLED = os.getenv("TM_CACHE_ENABLED", "1") == "1"

# --- Output ------------------------------------------------------------------
CANON_DIR = Path(os.getenv("TM_CANON_DIR", str(ROOT / "data" / "canon")))
IMAGES_DIR = Path(os.getenv("TM_IMAGES_DIR", str(ROOT / "data" / "images")))
SCHEMA_PATH = ROOT / "canon_schema.json"

# --- Tier-1 coverage ---------------------------------------------------------
# Top-5 leagues + second divisions, resolved via /competitions/search/{name}.
TIER_1_COMPETITIONS: list[str] = [
    "Premier League",
    "LaLiga",
    "Bundesliga",
    "Serie A",
    "Ligue 1",
    "Championship",
    "Segunda División",
    "2. Bundesliga",
    "Serie B",
    "Ligue 2",
]

# Top-20 national teams, resolved via /national-teams/most-valuable + search.
TIER_1_NATIONAL_TEAMS: list[str] = [
    "England", "France", "Spain", "Portugal", "Brazil",
    "Germany", "Netherlands", "Argentina", "Belgium", "Norway",
    "Senegal", "Morocco", "Türkiye", "Ivory Coast", "Ecuador",
    "Sweden", "Uruguay", "United States", "Switzerland", "Colombia",
]

# Pilot scope: a few clubs + one national team to verify the pipeline.
PILOT_CLUB_IDS: list[str] = ["583", "418"]  # PSG, Real Madrid
PILOT_NATIONAL_TEAM_IDS: list[str] = ["3377"]  # France

# --- User-Agent pool & default headers ---------------------------------------
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}