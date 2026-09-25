"""Daily ingest — the one script to Task-Schedule.

Runs (idempotently, in <5 min):
  1. Snapshot NEPSE sector index → data/sector_history.jsonl
  2. Snapshot macro (NEPSE + NIFTY) → data/macro_history.jsonl
  3. Pull /api/nepse/dividends → append new items to corporate-actions calendar
  4. Pull /api/nepse/announcements → append to news sentiment source
  5. Pull /api/nepse/floorsheet (all pages) → data/floorsheet/{YYYY-MM-DD}.jsonl
  6. Refresh signal accuracy ledger from journal

Wire this to Task Scheduler at market-close + 30 min (~4pm NPT).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(r"C:\Users\Dell\Desktop\fuse\A-Multi-Agent-Trading-Research-Desk")
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("daily_ingest")


def main() -> int:
    steps = 0
    errors = 0

    logger.info("=== Step 1: sector snapshot ===")
    try:
        from argos.data.nepse_sectors import snapshot_sectors_today
        s = snapshot_sectors_today()
        logger.info("  → %d sectors on %s", len(s["sectors"]), s["date"])
        steps += 1
    except Exception as e:
        logger.exception("sector snapshot failed: %s", e)
        errors += 1

    logger.info("=== Step 2: macro snapshot ===")
    try:
        from argos.data.macro_context import snapshot_macro_today
        m = snapshot_macro_today()
        logger.info("  → regime=%s  NEPSE=%s (%s)  NIFTY=%s (%s)",
                    m.regime, m.nepse_index, m.nepse_change_pct,
                    m.nifty_close, m.nifty_change_pct)
        steps += 1
    except Exception as e:
        logger.exception("macro snapshot failed: %s", e)
        errors += 1

    logger.info("=== Step 3: ingest corporate actions ===")
    try:
        from argos.data.corporate_actions import fetch_from_nepse_api, load_all
        appended = fetch_from_nepse_api()
        logger.info("  → +%d new dividends, %d total in calendar",
                    len(appended), len(load_all()))
        steps += 1
    except Exception as e:
        logger.exception("corporate-actions ingest failed: %s", e)
        errors += 1

    logger.info("=== Step 4: ingest announcements ===")
    try:
        from argos.data.announcements import ingest_announcements_today
        n = ingest_announcements_today()
        logger.info("  → %d announcements stored", n)
        steps += 1
    except Exception as e:
        logger.exception("announcements ingest failed: %s", e)
        errors += 1

    logger.info("=== Step 5: full floorsheet ingest ===")
    try:
        from argos.data.floorsheet import (
            fetch_floorsheet_from_nepse_api, ingest_floorsheet_rows,
        )
        rows = fetch_floorsheet_from_nepse_api(max_pages=300)
        ingest_floorsheet_rows(rows)
        logger.info("  → %d floorsheet rows persisted", len(rows))
        steps += 1
    except Exception as e:
        logger.exception("floorsheet ingest failed: %s", e)
        errors += 1

    logger.info("=== Step 6: refresh signal accuracy ledger ===")
    try:
        from argos.data.nepse_ohlcv import load_nepse_ohlcv
        from argos.learn.signal_ledger import update_from_journal
        upd = update_from_journal(REPO / "logs" / "decisions", load_nepse_ohlcv)
        logger.info("  → ledger has %d signals tracked", len(upd.signals))
        steps += 1
    except Exception as e:
        logger.exception("ledger refresh failed: %s", e)
        errors += 1

    logger.info("=== Done: %d steps completed, %d errors ===", steps, errors)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
