"""NASDAQ multi-ticker backtest runner.

Loops the universe defined in `data/universe/nasdaq_focus.json` (default:
NVDA, TSLA, ORCL) across the BACKTEST_START..BACKTEST_END window and
invokes the LangGraph pipeline once per (ticker, date). Journal output
lands under `logs/decisions/nasdaq_focus/{ticker}/{ticker}_{date}.jsonl`
so the eval harness can walk per-ticker directories cleanly.

Flags:
  --dry-run       print the plan (ticker×date count, est. cost) and exit
  --tickers X,Y   override the universe (comma-separated)
  --start/--end   override BACKTEST_START / BACKTEST_END
  --force         re-run dates that already have a journal file
  --skip-cached   default True — respect the LLM cache (re-runs are free)
  --workers N     future: parallel ticker workers (default 1, serial)

Cost gate:
  With ~3 tickers × ~250 trading days × ~8 LLM calls at gpt-4o mid-tier,
  expect ~$25–40 on a cold run and ~$0 on cached re-runs. The runner
  prints an estimated call count before starting; abort with Ctrl-C if
  something looks off.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env", override=True)

# Import after .env is loaded so MARKET is set before argos modules read it.
from argos.data.kronos_client import check_health as kronos_health  # noqa: E402
from argos.data.ohlcv_loader import load_ohlcv  # noqa: E402
from argos.graph import compile_graph, create_initial_state  # noqa: E402
from argos.journal import (  # noqa: E402
    append_record,
    build_record,
    date_already_journalled,
)
from argos.llm_cache import cache_stats  # noqa: E402
from argos.market import is_nasdaq  # noqa: E402
from argos.token_usage import start_decision as reset_tokens  # noqa: E402
from run_backtest import _build_ohlcv_summary, generate_trading_dates  # noqa: E402

DEFAULT_UNIVERSE_PATH = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("nasdaq_backtest")


def _load_universe(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    tickers = data.get("tickers")
    if not tickers:
        raise ValueError(f"Universe file {path} has no 'tickers' array")
    return list(tickers)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Argos NASDAQ multi-ticker backtest")
    p.add_argument("--tickers", default=None,
                   help="Comma-separated tickers (default: universe file)")
    p.add_argument("--start", default=os.environ.get("BACKTEST_START", "2024-01-02"))
    p.add_argument("--end", default=os.environ.get("BACKTEST_END", "2024-12-31"))
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE_PATH))
    p.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    p.add_argument("--cadence", default="weekly",
                   choices=("daily", "weekly", "biweekly", "monthly"),
                   help="Decision cadence: daily=every trading day, "
                        "weekly=every 5th (default), biweekly=every 10th, "
                        "monthly=every 21st. Wall-time budget scales linearly.")
    p.add_argument("--force", action="store_true",
                   help="Re-run dates that already have a journal file")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan and exit -- no LLM calls")
    return p.parse_args()


_CADENCE_STRIDE = {"daily": 1, "weekly": 5, "biweekly": 10, "monthly": 21}


def _apply_cadence(dates: list[str], cadence: str) -> list[str]:
    stride = _CADENCE_STRIDE[cadence]
    if stride <= 1:
        return dates
    return dates[::stride]


def _print_plan(tickers: list[str], dates: list[str], output_root: Path) -> None:
    n_calls_est = len(tickers) * len(dates) * 8  # ~8 LLM calls per (ticker, date)
    # Rough $ estimate for gpt-4o mid-tier: ~$0.005 per call on average
    est_cost_usd = n_calls_est * 0.005
    logger.info("-" * 60)
    logger.info("Plan: %d tickers × %d trading days = %d decision points",
                len(tickers), len(dates), len(tickers) * len(dates))
    logger.info("Est. LLM calls (cold run): ~%d", n_calls_est)
    logger.info("Est. cost (gpt-4o mid-tier, cold): ~$%.2f", est_cost_usd)
    logger.info("Output root: %s", output_root)
    stats = cache_stats()
    if stats.get("enabled"):
        logger.info("LLM cache: %s (%d rows)", stats.get("path"), stats.get("total_rows", 0))
    logger.info("-" * 60)


def _run_one(ticker: str, date: str, graph, output_dir: Path, force: bool) -> str:
    """Run one (ticker, date). Returns 'processed' | 'skipped' | 'errored'."""
    if date_already_journalled(output_dir, ticker, date) and not force:
        logger.info("  v cached journal: %s @ %s", ticker, date)
        return "skipped"

    t0 = time.time()
    try:
        ohlcv_data = load_ohlcv(ticker, date)
        ohlcv_summary = _build_ohlcv_summary(ohlcv_data)
        state = create_initial_state(
            ticker=ticker, as_of_date=date, ohlcv_window=ohlcv_data,
        )
        reset_tokens()  # per-decision token counter — read by journal.build_record
        final_state = graph.invoke(state)
        duration = time.time() - t0

        decision = final_state.get("final_decision")
        if decision:
            logger.info(
                "  -> %s @ %s: %s (conf %.2f, size %.2f, %.1fs)",
                ticker, date, decision["action"], decision["confidence"],
                decision["position_size"], duration,
            )
        else:
            logger.warning("  no final decision: %s @ %s", ticker, date)

        record = build_record(ticker, date, final_state, ohlcv_summary, duration)
        append_record(record, output_dir, ticker, date)
        return "processed"

    except Exception as e:  # noqa: BLE001
        duration = time.time() - t0
        logger.error("  x %s @ %s failed: %s", ticker, date, e)
        err_record = {
            "attempt_id": None,
            "attempted_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "ticker": ticker, "date": date,
            "duration_s": round(duration, 3),
            "error": str(e), "final_decision": None,
        }
        append_record(err_record, output_dir, ticker, date)
        return "errored"


def main():
    args = _parse_args()

    if not is_nasdaq():
        logger.warning("MARKET is not 'nasdaq' — set MARKET=nasdaq in .env "
                       "before running the NASDAQ backtest.")

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = _load_universe(Path(args.universe))
    all_dates = generate_trading_dates(args.start, args.end)
    dates = _apply_cadence(all_dates, args.cadence)
    output_root = Path(args.output_root)

    logger.info("Cadence: %s (%d dates from %d trading days)",
                args.cadence, len(dates), len(all_dates))
    _print_plan(tickers, dates, output_root)

    if args.dry_run:
        logger.info("Dry run — exiting without invoking the graph.")
        return

    kronos_url = os.environ.get("KRONOS_ENDPOINT_URL")
    if kronos_url:
        try:
            health = kronos_health(kronos_url)
            logger.info("Kronos endpoint OK: model=%s device=%s",
                        health.get("model"), health.get("device"))
        except Exception as e:  # noqa: BLE001
            logger.warning("Kronos endpoint unreachable: %s — forecasts stubbed", e)

    graph = compile_graph()

    counts = {"processed": 0, "skipped": 0, "errored": 0}
    total_start = time.time()
    for ti, ticker in enumerate(tickers, 1):
        ticker_dir = output_root / ticker
        logger.info("=" * 60)
        logger.info("Ticker %d/%d: %s -> %s", ti, len(tickers), ticker, ticker_dir)
        logger.info("=" * 60)
        for di, date in enumerate(dates, 1):
            logger.info("[%s %d/%d] %s", ticker, di, len(dates), date)
            outcome = _run_one(ticker, date, graph, ticker_dir, args.force)
            counts[outcome] += 1

    total_s = time.time() - total_start
    logger.info("-" * 60)
    logger.info("Backtest complete in %.1f min: processed=%d, skipped=%d, errored=%d",
                total_s / 60, counts["processed"], counts["skipped"], counts["errored"])
    logger.info("Cache after run: %s", cache_stats())


if __name__ == "__main__":
    main()
