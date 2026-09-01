"""Full crawl orchestrator: runs all steps in sequence with retry.

Steps:
    1. fetch_leagues.py          - club lists by league (ALL leagues)
    2. fetch_club_profiles.py    - club profiles (colors, logo, tier)
    3. fetch_club_rosters.py     - club rosters (imageUrl)
    4. fetch_national_teams.py   - national teams (ALL 250)
    5. download_pilot_images.py  - faces, logos, emblems, flags
    6. build_pilot_json.py       - assemble final JSON
    7. retry_failures.py         - retry everything that failed

Usage:
    python pilot_crawl.py              # all steps
    python pilot_crawl.py --step 3     # only step 3
    python pilot_crawl.py --step 3-5   # steps 3 through 5
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("pilot_crawl")

SCRIPTS = [
    ("1. Fetch league club lists", "fetch_leagues.py"),
    ("2. Fetch club profiles", "fetch_club_profiles.py"),
    ("3. Fetch club rosters", "fetch_club_rosters.py --refresh"),
    ("4. Fetch national teams", "fetch_national_teams.py --top 250"),
    ("5. Download images", "download_pilot_images.py"),
    ("6. Build full JSON", "build_pilot_json.py"),
]

RETRY_SCRIPT = "retry_failures.py"
MAX_RETRIES = 5


def run_script(cmd: str) -> bool:
    result = subprocess.run(
        [sys.executable] + cmd.split(),
        cwd=str(Path(__file__).resolve().parent),
    )
    return result.returncode == 0


def run_step(step_num: int, name: str, cmd: str) -> bool:
    logger.info("STEP %d: %s", step_num, name)
    start = time.time()
    ok = run_script(cmd)
    elapsed = time.time() - start
    if ok:
        logger.info("STEP %d DONE (%.0fs)", step_num, elapsed)
    else:
        logger.error("STEP %d FAILED (exit code)", step_num)
    return ok


def verify_and_retry() -> bool:
    """Run retry_failures.py until everything is downloaded or max retries hit."""
    for attempt in range(1, MAX_RETRIES + 1):
        logger.info("RETRY ATTEMPT %d/%d", attempt, MAX_RETRIES)
        start = time.time()
        ok = run_script(RETRY_SCRIPT)
        elapsed = time.time() - start
        if ok:
            logger.info("RETRY DONE (%.0fs) — all data complete", elapsed)
            return True
        else:
            logger.warning("RETRY found failures (%.0fs), will retry...", elapsed)
            time.sleep(5)
    logger.error("MAX RETRIES REACHED — some data may still be missing")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Pilot crawl orchestrator.")
    parser.add_argument("--step", default=None,
                        help="step(s): '3' or '3-5' (default: all)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.step:
        if "-" in args.step:
            s, e = map(int, args.step.split("-"))
            steps = list(range(s, e + 1))
        else:
            steps = [int(args.step)]
    else:
        steps = list(range(1, len(SCRIPTS) + 1))

    logger.info("pilot crawl: steps %s", steps)
    start = time.time()
    failed = []

    for i, (name, cmd) in enumerate(SCRIPTS, 1):
        if i not in steps:
            continue
        if not run_step(i, name, cmd):
            failed.append(i)
            break

    # Retry loop (only if no script failed)
    if not failed:
        logger.info("ALL PRIMARY STEPS DONE — starting retry verification")
        verify_and_retry()

    elapsed = time.time() - start
    if failed:
        logger.error("FAILED at step(s) %s (%.0fs)", failed, elapsed)
        sys.exit(1)
    else:
        logger.info("ALL DONE (%.0fs)", elapsed)


if __name__ == "__main__":
    main()
