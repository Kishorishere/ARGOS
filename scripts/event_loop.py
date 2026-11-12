"""Argos event loop — the live consumer of argos.triggers.

Walks the watchlist every N seconds. For each ticker it runs the trigger
library; if anything fires and `--autofire` is set, it invokes the
LangGraph committee on today's data and journals under
`logs/decisions/live/<market>/<ticker>/`.

Design notes:
- Default mode is observational (no LLM calls). You see what would have
  fired a committee, nothing is billed. Add --autofire once you trust
  the rule set on your watchlist.
- Already-fired triggers for a (ticker, as_of) that already has a live
  journal are ignored unless --force, so the loop is safe to restart
  without re-billing.
- Scheduler is a plain sleep loop. If you want cron-like precision,
  wrap this in your favourite service manager; the point here is
  predictability, not scheduler features.
- Notifications: emit a single structured line to STDOUT per fire; pipe
  the loop's output to tee / Logfire / webhook. Phase E wires a proper
  notification channel (email/webhook) through an env flag.

Usage:
    python scripts/event_loop.py                        # once, print, exit
    python scripts/event_loop.py --watch 60             # every 60s, forever
    python scripts/event_loop.py --watch 60 --autofire  # and run the committee on fires
    python scripts/event_loop.py --tickers NVDA,TSLA    # override universe
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env", override=True)

from argos.triggers import Trigger, check_all  # noqa: E402

DEFAULT_UNIVERSE_PATH = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
LIVE_DIR = REPO_ROOT / "logs" / "decisions" / "live"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("event_loop")


# ── CLI ─────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Argos event-driven trigger loop")
    p.add_argument("--watch", type=int, default=0,
                   help="Seconds between scans. 0 = run once and exit (default).")
    p.add_argument("--tickers", default=None,
                   help="Comma-separated override (default: data/universe/nasdaq_focus.json)")
    p.add_argument("--autofire", action="store_true",
                   help="When triggers fire, invoke the committee and journal under "
                        "logs/decisions/live/. Default is observational (no LLM calls).")
    p.add_argument("--market", default="nasdaq",
                   help="Market segment for live journals (nasdaq|nepse). Default nasdaq.")
    p.add_argument("--force", action="store_true",
                   help="Re-run tickers that already have a live journal for today.")
    return p.parse_args()


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _universe(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if DEFAULT_UNIVERSE_PATH.exists():
        return list(json.loads(DEFAULT_UNIVERSE_PATH.read_text()).get("tickers") or [])
    return []


def _already_journalled(market: str, ticker: str, as_of: str) -> bool:
    p = LIVE_DIR / market / ticker.upper() / f"{ticker.upper()}_{as_of}.jsonl"
    return p.exists()


# ── Firing the committee on a set of triggers ──────────────────────────────

def _fire_committee(market: str, ticker: str, as_of: str, triggers: list[Trigger]) -> str | None:
    """Invoke the LangGraph pipeline on (ticker, as_of) with live data,
    attach the triggers list to initial state (for the journal), and
    journal under logs/decisions/live/<market>/<ticker>/.

    The graph itself does NOT yet read state['triggers'] — that's a
    deliberate Phase B boundary (gate prompt change needs an A/B). The
    triggers ride along in state so the record builder journals them
    for later analysis.
    """
    try:
        from argos.data.ohlcv_loader import load_ohlcv
        from argos.graph import compile_graph, create_initial_state
        from argos.journal import append_record, build_record
        from argos.token_usage import start_decision
    except Exception as e:  # noqa: BLE001
        logger.error("event_loop: cannot import pipeline: %s", e)
        return None

    try:
        window = load_ohlcv(ticker, as_of, allow_today=True)
    except Exception as e:  # noqa: BLE001
        logger.error("event_loop: OHLCV load failed for %s: %s", ticker, e)
        return None

    state = create_initial_state(ticker=ticker, as_of_date=as_of, ohlcv_window=window)
    # Additive, optional state field. Pipeline nodes ignore it today.
    state["triggers"] = [t.to_dict() for t in triggers]  # type: ignore[typeddict-unknown-key]

    start_decision()
    t0 = time.time()
    try:
        final = compile_graph().invoke(state)
    except Exception as e:  # noqa: BLE001
        logger.error("event_loop: pipeline raised on %s: %s", ticker, e)
        return None
    duration = time.time() - t0

    record = build_record(ticker, as_of, final, _ohlcv_summary(window), duration)
    # Attach triggers to the record so Phase D can slice "decisions that
    # came from a trigger" vs "decisions from the calendar loop".
    record["triggers"] = [t.to_dict() for t in triggers]

    out_dir = LIVE_DIR / market / ticker.upper()
    path = append_record(record, out_dir, ticker, as_of)
    logger.info("  journalled %s → %s", ticker, path.relative_to(REPO_ROOT))
    return str(path.relative_to(REPO_ROOT))


def _ohlcv_summary(window: dict) -> dict:
    rows = window.get("ohlcv") or []
    if not rows:
        return {"rows": 0, "date_range": "N/A", "last_5_closes": []}
    last5 = rows[-5:] if len(rows) >= 5 else rows
    return {
        "rows": window.get("rows", len(rows)),
        "date_range": window.get("date_range", "N/A"),
        "last_5_closes": [r["close"] for r in last5],
        "last_close": rows[-1]["close"],
        "last_date": rows[-1]["date"],
    }


# ── One scan ────────────────────────────────────────────────────────────────

def _scan_once(args: argparse.Namespace) -> None:
    as_of = _today_iso()
    tickers = _universe(args)
    if not tickers:
        logger.warning("empty universe — nothing to scan")
        return
    logger.info("── scan %s @ %s · %d tickers · autofire=%s",
                "LIVE" if args.autofire else "DRY", as_of, len(tickers), args.autofire)
    for ticker in tickers:
        try:
            fires = check_all(ticker, as_of)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s: trigger scan failed: %s", ticker, e)
            continue
        if not fires:
            logger.info("%s: quiet", ticker)
            continue
        kinds = ", ".join(f"{t.kind}({t.direction})" for t in fires)
        logger.info("%s: %d triggers · %s", ticker, len(fires), kinds)
        for t in fires:
            logger.info("    · %s", t.detail)
        if not args.autofire:
            continue
        if not args.force and _already_journalled(args.market, ticker, as_of):
            logger.info("    (skipped: live journal already exists for %s@%s)", ticker, as_of)
            continue
        _fire_committee(args.market, ticker, as_of, fires)


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    _scan_once(args)
    if args.watch > 0:
        logger.info("watching every %ds — Ctrl-C to stop", args.watch)
        while True:
            try:
                time.sleep(args.watch)
                _scan_once(args)
            except KeyboardInterrupt:
                logger.info("stopped by user")
                break


if __name__ == "__main__":
    main()
