"""Multi-asset screener backtest (U7 driver).

For each trading date in a window:
  1. Run the screener across NEPSE universe → top-K bullish + top-K bearish
  2. Run the full 7-agent Argos graph on each of those top candidates
  3. Journal every decision

Delivers ~3-10 quality directional calls per day instead of ~1/week per
ticker. Enables statistically-meaningful hit-rate measurement without
running one ticker for 700 days.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(r"C:\Users\Dell\Desktop\fuse\A-Multi-Agent-Trading-Research-Desk")
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env", override=True)

from argos.data.kronos_client import check_health as kronos_health
from argos.data.nepse_ohlcv import load_nepse_ohlcv
from argos.graph import compile_graph, create_initial_state
from argos.journal import append_record, build_record, date_already_journalled
from argos.screener import rank_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("screener_backtest")

OUTPUT_DIR = REPO / "logs" / "decisions"


def _run_one(graph, ticker: str, date: str, force: bool) -> str:
    """Run the full graph for one (ticker, date) and journal. Returns action."""
    if date_already_journalled(OUTPUT_DIR, ticker, date) and not force:
        logger.info("  skip %s @ %s (already journalled)", ticker, date)
        return "skip"

    t0 = time.time()
    try:
        ohlcv = load_nepse_ohlcv(ticker, date, lookback_days=800)
        summary = {
            "rows": ohlcv["rows"], "date_range": ohlcv["date_range"],
            "last_close": ohlcv["ohlcv"][-1]["close"],
            "last_date": ohlcv["ohlcv"][-1]["date"],
            "last_5_closes": [r["close"] for r in ohlcv["ohlcv"][-5:]],
        }
        state = create_initial_state(ticker=ticker, as_of_date=date, ohlcv_window=ohlcv)
        state["max_debate_rounds"] = 1
        final = graph.invoke(state)
        duration = time.time() - t0

        dec = final.get("final_decision") or {}
        rec = build_record(ticker, date, final, summary, duration)
        append_record(rec, OUTPUT_DIR, ticker, date)
        logger.info("  %s @ %s → %s (conf %.2f size %.3f) %.1fs",
                    ticker, date, dec.get("action"), dec.get("confidence", 0),
                    dec.get("position_size", 0), duration)
        return dec.get("action", "hold")
    except Exception as e:
        logger.exception("  %s @ %s FAILED: %s", ticker, date, e)
        return "error"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--universe", nargs="+",
                    help="Explicit symbol list (defaults to today's NEPSE screener universe)")
    ap.add_argument("--top-bull", type=int, default=3, help="Top-K bullish per date")
    ap.add_argument("--top-bear", type=int, default=3, help="Top-K bearish per date")
    ap.add_argument("--max-rounds", type=int, default=1)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    os.environ["MAX_DEBATE_ROUNDS"] = str(args.max_rounds)

    # Health checks
    if os.environ.get("KRONOS_ENDPOINT_URL"):
        try:
            h = kronos_health(os.environ["KRONOS_ENDPOINT_URL"])
            logger.info("Kronos alive: %s", h)
        except Exception as e:
            logger.warning("Kronos unreachable: %s", e)

    # Discover trading dates by scanning any one symbol's history
    anchor_symbol = (args.universe or ["ADBL"])[0]
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    days_needed = (end_dt - datetime.strptime(args.start, "%Y-%m-%d")).days + 400
    anchor_data = load_nepse_ohlcv(anchor_symbol, args.end, lookback_days=days_needed)
    trading_dates = [r["date"] for r in anchor_data["ohlcv"]
                     if args.start <= r["date"] <= args.end]
    logger.info("Discovered %d NEPSE trading dates in %s..%s",
                len(trading_dates), args.start, args.end)

    graph = compile_graph()
    total_start = time.time()
    all_actions: dict[str, int] = {"buy": 0, "sell": 0, "hold": 0, "skip": 0, "error": 0}

    for i, date in enumerate(trading_dates, 1):
        logger.info("=== Date %d/%d: %s ===", i, len(trading_dates), date)
        # Screen the universe for this date
        ranking = rank_universe(
            date,
            symbols=args.universe,  # None → today's NEPSE snapshot
            top_k_bullish=args.top_bull,
            top_k_bearish=args.top_bear,
        )
        candidates = [r["symbol"] for r in ranking["top_bullish"]] \
                   + [r["symbol"] for r in ranking["top_bearish"]]
        logger.info("  screener picked %d bullish + %d bearish = %s",
                    len(ranking["top_bullish"]), len(ranking["top_bearish"]),
                    ", ".join(candidates))
        for sym in candidates:
            action = _run_one(graph, sym, date, args.force)
            all_actions[action] = all_actions.get(action, 0) + 1

    wall = time.time() - total_start
    logger.info("Done: %d dates × ~%d candidates → actions: %s   total_wall=%.1fs",
                len(trading_dates),
                (args.top_bull + args.top_bear),
                all_actions, wall)


if __name__ == "__main__":
    main()
