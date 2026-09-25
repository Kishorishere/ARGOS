"""
Backtest Runner — Argos.

Iterates dates in a window, invokes the graph once per date, and appends a
structured decision journal record (per-date JSONL, append-only) with:
  - OHLCV summary
  - Analyst prompts (exact system + user each agent received)
  - Analyst signals + FULL IndicatorSnapshot for the technicals agent
  - Every fired indicator with its value/threshold/direction/weight/contribution
  - Kronos forecast (with uncertainty bands when available)
  - Debate transcript (round-tagged)
  - Risk verdict
  - Final decision
  - Portfolio state passed to the risk manager
  - Errors + attempt_id + argos_version (git SHA)

Flags:
  --force      re-run a date that already has a journal file (still appends)
  --simulate   apply each decision to a paper portfolio (see argos.simulate)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

from argos.data.kronos_client import check_health as kronos_health
from argos.data.ohlcv_loader import load_ohlcv
from argos.graph import compile_graph, create_initial_state
from argos.journal import (
    append_record,
    build_record,
    date_already_journalled,
)

# ── Config ───────────────────────────────────────────────────────────────

TICKER = os.environ.get("BACKTEST_TICKER", "AAPL")
START_DATE = os.environ.get("BACKTEST_START", "2024-01-02")
END_DATE = os.environ.get("BACKTEST_END", "2024-01-31")
OUTPUT_DIR = Path("logs/decisions")
KRONOS_URL = os.environ.get("KRONOS_ENDPOINT_URL")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backtest")


def generate_trading_dates(start: str, end: str) -> list[str]:
    """Business-day dates between start and end (inclusive), weekends skipped.

    Full exchange-holiday awareness is deferred until NEPSE calendar integration
    lands with the terminal wiring — for a research-only tool, weekday filtering
    is enough to keep the ~1-2% of holiday dates from wasting LLM quota; those
    will simply error out cleanly on empty OHLCV and get logged.
    """
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    dates = []
    current = start_dt
    while current <= end_dt:
        if current.weekday() < 5:
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def _build_ohlcv_summary(ohlcv_data: dict) -> dict:
    records = ohlcv_data.get("ohlcv", [])
    if not records:
        return {"rows": 0, "date_range": "N/A", "last_5_closes": []}
    last_5 = records[-5:] if len(records) >= 5 else records
    return {
        "rows": ohlcv_data.get("rows", len(records)),
        "date_range": ohlcv_data.get("date_range", "N/A"),
        "last_5_closes": [r["close"] for r in last_5],
        "last_close": records[-1]["close"],
        "last_date": records[-1]["date"],
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Argos backtest driver")
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-run dates that already have a journal file (still appends)",
    )
    p.add_argument(
        "--simulate",
        action="store_true",
        help="Apply each decision to a paper portfolio and print end-of-window stats",
    )
    p.add_argument("--ticker", default=TICKER, help="Ticker to backtest (default: $BACKTEST_TICKER or AAPL)")
    p.add_argument("--start", default=START_DATE, help="Start date YYYY-MM-DD")
    p.add_argument("--end", default=END_DATE, help="End date YYYY-MM-DD")
    return p.parse_args()


def run_backtest():
    args = _parse_args()
    ticker = args.ticker
    logger.info("Argos backtest: ticker=%s, dates=%s to %s (force=%s, simulate=%s)",
                ticker, args.start, args.end, args.force, args.simulate)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if KRONOS_URL:
        try:
            health = kronos_health(KRONOS_URL)
            logger.info("Kronos endpoint: %s (model=%s, device=%s)",
                        KRONOS_URL, health.get("model"), health.get("device"))
        except Exception as e:
            logger.warning("Kronos endpoint unreachable at %s: %s — forecasts will be stubbed",
                           KRONOS_URL, e)
    else:
        logger.info("KRONOS_ENDPOINT_URL not set — Kronos forecasts will be stubbed")

    graph = compile_graph()
    dates = generate_trading_dates(args.start, args.end)
    logger.info("Generated %d trading dates to evaluate", len(dates))

    processed = 0
    skipped = 0
    errored = 0

    for i, date in enumerate(dates, 1):
        logger.info("─── Date %d/%d: %s ───", i, len(dates), date)

        if date_already_journalled(OUTPUT_DIR, ticker, date) and not args.force:
            logger.info("Journal exists for %s @ %s — skipping (pass --force to re-run)",
                        ticker, date)
            skipped += 1
            continue

        t0 = time.time()
        try:
            ohlcv_data = load_ohlcv(ticker, date)
            ohlcv_summary = _build_ohlcv_summary(ohlcv_data)
            state = create_initial_state(
                ticker=ticker,
                as_of_date=date,
                ohlcv_window=ohlcv_data,
            )
            final_state = graph.invoke(state)
            duration = time.time() - t0

            decision = final_state.get("final_decision")
            if decision:
                logger.info(
                    "Decision for %s on %s: %s (confidence: %.2f, position_size: %.2f)",
                    ticker, date, decision["action"], decision["confidence"], decision["position_size"],
                )
            else:
                logger.warning("No final decision produced for %s on %s", ticker, date)

            record = build_record(ticker, date, final_state, ohlcv_summary, duration)
            path = append_record(record, OUTPUT_DIR, ticker, date)
            logger.info("Journal appended → %s (attempt_id=%s, duration=%.1fs, indicators fired=%d)",
                        path, record["attempt_id"], duration, len(record["indicator_signals_fired"]))
            processed += 1

        except Exception as e:
            duration = time.time() - t0
            logger.error("Error processing %s on %s: %s", ticker, date, e)
            err_record = {
                "attempt_id": None,
                "attempted_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "ticker": ticker,
                "date": date,
                "duration_s": round(duration, 3),
                "error": str(e),
                "final_decision": None,
            }
            append_record(err_record, OUTPUT_DIR, ticker, date)
            errored += 1

    logger.info(
        "Backtest complete: %d processed, %d skipped (already journalled), %d errored → %s",
        processed, skipped, errored, OUTPUT_DIR,
    )

    if args.simulate:
        from argos.simulate import replay
        stats = replay(OUTPUT_DIR, ticker)
        logger.info("Paper simulation complete: %s", stats)


if __name__ == "__main__":
    run_backtest()
