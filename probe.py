"""Probe the real Transfermarkt rate ceilings before any mass crawl.

Three probes:

  --html         HTML endpoints via the local API (/clubs/{id}/players). Each
                 API call is one live TM request, so the observed rate is the
                 TM rate. Levels escalate conservatively with cooldowns.

  --html-direct  Direct probe of www.transfermarkt.com (bypasses the API, which
                 itself caps throughput at its threadpool ~40 threads). Finds
                 the real TM/Cloudflare ceiling and classifies the block type
                 (429 / 403 / challenge / connection reset) — levels escalate
                 until the first block, then stop.

  --cdn          Image downloads from img.a.transfermarkt.technology (CDN),
                 which is NOT throttled like www.transfermarkt.com.

Usage:
    python probe.py --html
    python probe.py --html-direct
    python probe.py --cdn

The recommended safe rate (70% of the highest clean level) is printed and
documented in docs/wiki/конвейер.md.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import time

import requests

from client import Client
from config import API_BASE

logger = logging.getLogger("scraper.probe")

HTML_LEVELS = [float(x) for x in
               os.getenv("TM_PROBE_LEVELS", "0.5,1.0,1.5,2.0,3.0").split(",")]
DIRECT_LEVELS = [float(x) for x in
                 os.getenv("TM_PROBE_DIRECT_LEVELS", "5,10,20,40,60,75").split(",")]
CDN_LEVELS = [float(x) for x in
              os.getenv("TM_PROBE_CDN_LEVELS", "2.0,5.0,10.0").split(",")]
TOTAL = int(os.getenv("TM_PROBE_TOTAL", "20"))
COOLDOWN = float(os.getenv("TM_PROBE_COOLDOWN", "60"))  # seconds between levels

DIRECT_URL = os.getenv("TM_PROBE_DIRECT_URL", "https://www.transfermarkt.com/")

THROTTLE_STATUSES = {403, 429, 500, 502, 503, 504, 520, 522, 524}
BLOCK_SIGNATURES = ("cf-chl-", "attention required", "are you a human",
                    "cloudflare", "challenge", "captcha")

# A few real CDN image URLs (filled by --cdn probe bootstrap).
CDN_URLS: list[str] = []


def _is_blocked_body(text: str) -> bool:
    head = (text or "")[:8000].lower()
    return any(sig in head for sig in BLOCK_SIGNATURES)


def _classify(status: int, resp, body_sig: bool) -> str:
    """Short label for a block response."""
    if status == 429:
        ra = (resp.headers.get("Retry-After") or "?") if resp else "?"
        return f"429 retry-after={ra}"
    if status == 403:
        return "403 body=challenge" if body_sig else "403 body=forbidden"
    if status in THROTTLE_STATUSES:
        return f"{status} (server)"
    return f"{status}"


def _probe_level(client: Client, urls_or_paths: list[str], bucket: str) -> dict:
    counts = {"ok": 0, "throttled": 0, "errors": 0, "blocked_body": 0,
              "first_throttle_at": None, "statuses": {}}
    for i in range(1, TOTAL + 1):
        target = urls_or_paths[i % len(urls_or_paths)]
        try:
            if target.startswith("http"):
                url = target
            elif bucket == "html":
                url = f"{client.api_base}/{target.lstrip('/')}"
            else:
                url = target
            resp = client.request("GET", url, bucket=bucket, use_cache=False, max_retries=1)
            status = resp.status_code
            body_sig = _is_blocked_body(resp.text)
            counts["statuses"][status] = counts["statuses"].get(status, 0) + 1
            if status in THROTTLE_STATUSES or body_sig:
                counts["throttled"] += 1
                counts["blocked_body"] += body_sig
                counts["first_throttle_at"] = counts["first_throttle_at"] or i
                marker = f"BLOCK {_classify(status, resp, body_sig)}"
            elif 200 <= status < 400:
                counts["ok"] += 1
                marker = f"ok {status}"
            else:
                counts["errors"] += 1
                marker = f"? {status}"
            print(f"  [{i:>3}/{TOTAL}] {marker}", flush=True)
        except (requests.exceptions.RequestException, RuntimeError) as exc:
            counts["errors"] += 1
            counts["first_throttle_at"] = counts["first_throttle_at"] or i
            print(f"  [{i:>3}/{TOTAL}] EXC {type(exc).__name__}: {exc}", flush=True)
    return counts


def probe_html() -> None:
    print(f"HTML probe via API {API_BASE}  (levels {HTML_LEVELS} rps, {TOTAL}/level)")
    summary = []
    for rps in HTML_LEVELS:
        client = Client(html_rps=rps, html_burst=1, max_retries=1,
                        concurrency=1, circuit_fails=999)
        print(f"\n=== HTML @ {rps:.2f} rps x {TOTAL} ===")
        counts = _probe_level(client, ["clubs/583/players"], "html")
        summary.append((rps, counts))
        blocked = counts["throttled"] + counts["errors"]
        if blocked >= max(2, TOTAL // 10):
            print(f"  -> throttling/errors started at {rps} rps "
                  f"(first at #{counts['first_throttle_at']})")
        else:
            print(f"  -> clean at {rps} rps: {counts}")
        if rps != HTML_LEVELS[-1]:
            print(f"  cooldown {COOLDOWN}s ...")
            time.sleep(COOLDOWN)
    _report("HTML", summary)


def probe_html_direct() -> None:
    """Direct TM probe: finds the real ceiling and stops at the first block."""
    print(f"DIRECT TM probe {DIRECT_URL} (levels {DIRECT_LEVELS} rps, {TOTAL}/level, "
          f"stops at first block)")
    summary: list[tuple[float, dict]] = []
    for rps in DIRECT_LEVELS:
        client = Client(html_rps=rps, html_burst=1, max_retries=1,
                        concurrency=1, circuit_fails=999)
        print(f"\n=== DIRECT @ {rps:.2f} rps x {TOTAL} ===")
        counts = _probe_level(client, [DIRECT_URL], "html")
        summary.append((rps, counts))
        blocked = counts["throttled"] + counts["errors"]
        if blocked >= max(2, TOTAL // 10):
            print(f"  -> FIRST BLOCK at {rps} rps "
                  f"(request #{counts['first_throttle_at']}, "
                  f"throttled={counts['throttled']} err={counts['errors']})")
            _report("DIRECT HTML", summary)
            return
        print(f"  -> clean at {rps} rps: ok={counts['ok']} "
              f"statuses={counts['statuses']}")
        if rps != DIRECT_LEVELS[-1]:
            print(f"  cooldown {COOLDOWN}s ...")
            time.sleep(COOLDOWN)
    _report("DIRECT HTML", summary)


def probe_cdn() -> None:
    print(f"CDN probe (levels {CDN_LEVELS} rps, {TOTAL}/level)")
    client = Client(max_retries=1, concurrency=1, circuit_fails=999)
    urls = _bootstrap_cdn_urls(client)
    if not urls:
        print("No CDN URLs to probe; aborting.")
        return
    summary = []
    for rps in CDN_LEVELS:
        c = Client(cdn_rps=rps, cdn_burst=1, max_retries=1, concurrency=1,
                   circuit_fails=999)
        print(f"\n=== CDN @ {rps:.2f} rps x {TOTAL} ===")
        counts = _probe_level(c, urls, "cdn")
        summary.append((rps, counts))
        blocked = counts["throttled"] + counts["errors"]
        if blocked >= max(2, TOTAL // 10):
            print(f"  -> throttling/errors started at {rps} rps "
                  f"(first at #{counts['first_throttle_at']})")
        else:
            print(f"  -> clean at {rps} rps: {counts}")
        if rps != CDN_LEVELS[-1]:
            print(f"  cooldown {COOLDOWN}s ...")
            time.sleep(COOLDOWN)
    _report("CDN", summary)


def _bootstrap_cdn_urls(client: Client) -> list[str]:
    """Collect a handful of real distinct CDN image URLs via the API."""
    urls: list[str] = []
    for path in ("clubs/583/profile", "clubs/418/profile", "national-teams/3377/profile"):
        try:
            data = client.api(path)
            image = data.get("image")
            if image:
                urls.append(image)
        except Exception as exc:
            print(f"  bootstrap {path} failed: {exc}")
    print(f"bootstrap CDN urls: {len(urls)}")
    for u in urls:
        print(f"  {u}")
    return urls


def _report(name: str, summary: list[tuple[float, dict]]) -> None:
    print(f"\n=================== {name} ITОГ ===================")
    safe_rps = None
    for rps, counts in summary:
        ok_ratio = counts["ok"] / TOTAL
        flag = "OK" if counts["throttled"] + counts["errors"] == 0 else \
            f"BLOCKED (throttled={counts['throttled']} err={counts['errors']})"
        print(f"  {rps:>5.2f} rps . ok={ok_ratio:.0%} . {flag} . statuses={counts['statuses']}")
        if counts["throttled"] + counts["errors"] == 0:
            safe_rps = rps
    if safe_rps:
        recommended = safe_rps * 0.7
        print(f"\nRecommendation {name}: {recommended:.2f} rps "
              f"(70% of ceiling {safe_rps} rps)")
    else:
        print(f"\n{name}: throttled/errors at every level — stop and wait, or "
              "the IP may already be under sanctions.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe Transfermarkt rate ceilings.")
    parser.add_argument("--html", action="store_true")
    parser.add_argument("--html-direct", action="store_true")
    parser.add_argument("--cdn", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    random.seed(20260829)
    if args.html_direct:
        probe_html_direct()
    elif args.cdn:
        probe_cdn()
    else:
        probe_html()


if __name__ == "__main__":
    main()