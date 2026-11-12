"""Paper-trading loop — live data, no money moving, full audit trail.

Same shape as scripts/event_loop.py but routes every committee fire to
the `paper` stage. Keeps a small paper-portfolio JSON file so a
subsequent scrape can score the paper arm's realized P&L against the
live arm's — the difference is purely the trader-override channel from
Phase E.

Files produced:
  logs/decisions/paper/<market>/<TICKER>/<TICKER>_<DATE>.jsonl
  data/paper_portfolio.json      (cash, positions, trade log)
  logs/paper_trades.jsonl        (append-only tape)

The pipeline itself is the same graph; the stage is purely a label and a
journal directory. That's intentional — paper and live produce identical
LLM behaviour, so the only difference is whether the record is tagged
`paper` (no money) or `live` (would be money, if we ever shipped).

Usage:
    python scripts/paper_loop.py --watch 300           # poll every 5 min
    python scripts/paper_loop.py --tickers NVDA,TSLA   # watchlist override
    python scripts/paper_loop.py --dry-run             # scan triggers, don't fire the committee
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env", override=True)

from argos.triggers import Trigger, check_all  # noqa: E402

PAPER_DIR = REPO_ROOT / "logs" / "decisions" / "paper"
PORTFOLIO_PATH = REPO_ROOT / "data" / "paper_portfolio.json"
TRADE_TAPE = REPO_ROOT / "logs" / "paper_trades.jsonl"

DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
STARTING_CASH = 100_000.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("paper_loop")


# ── Portfolio ──────────────────────────────────────────────────────────────

def _load_portfolio() -> dict:
    if PORTFOLIO_PATH.exists():
        try:
            return json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("portfolio file corrupt; starting fresh")
    return {"cash": STARTING_CASH, "positions": {}, "history": []}


def _save_portfolio(p: dict) -> None:
    PORTFOLIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_PATH.write_text(json.dumps(p, indent=2, default=str), encoding="utf-8")


def _apply_decision(pf: dict, ticker: str, as_of: str, action: str, size: float, price: float | None) -> dict | None:
    """Toy fill model — fill at the last close we know. Returns the trade
    dict that was tape'd, or None if nothing moved."""
    action = (action or "").lower()
    if action not in ("buy", "sell") or price is None or size <= 0:
        return None
    trade = {
        "id": str(uuid.uuid4()),
        "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticker": ticker.upper(),
        "as_of": as_of,
        "action": action,
        "position_size_pct_nav": size,
        "fill_price": price,
    }
    pos = pf["positions"].setdefault(ticker.upper(), {"qty": 0, "avg": 0.0})
    if action == "buy":
        nav = pf["cash"] + sum(p["qty"] * p["avg"] for p in pf["positions"].values())
        dollars = max(0.0, nav * size)
        qty = dollars / price
        new_qty = pos["qty"] + qty
        pos["avg"] = ((pos["avg"] * pos["qty"]) + (price * qty)) / new_qty if new_qty > 0 else price
        pos["qty"] = new_qty
        pf["cash"] -= qty * price
        trade["shares"] = qty
    else:  # sell — toy: close the full position
        qty = pos["qty"]
        if qty <= 0:
            return None
        pf["cash"] += qty * price
        pos["qty"] = 0
        pos["avg"] = 0.0
        trade["shares"] = qty
    pf["history"].append(trade)
    return trade


def _tape(trade: dict) -> None:
    TRADE_TAPE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_TAPE, "a", encoding="utf-8") as f:
        f.write(json.dumps(trade, default=str) + "\n")


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Paper-trading loop (live data, no money)")
    p.add_argument("--watch", type=int, default=0, help="Seconds between scans. 0 = one shot and exit.")
    p.add_argument("--tickers", default=None, help="Comma-separated (default: universe)")
    p.add_argument("--market", default="nasdaq")
    p.add_argument("--dry-run", action="store_true", help="Scan triggers, don't fire the committee.")
    p.add_argument("--force", action="store_true", help="Re-fire even if today's paper journal already exists.")
    return p.parse_args()


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _universe(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if DEFAULT_UNIVERSE.exists():
        return list(json.loads(DEFAULT_UNIVERSE.read_text()).get("tickers") or [])
    return []


def _already_journalled(market: str, ticker: str, as_of: str) -> bool:
    return (PAPER_DIR / market / ticker.upper() / f"{ticker.upper()}_{as_of}.jsonl").exists()


def _fire_committee(market: str, ticker: str, as_of: str, triggers: list[Trigger]) -> dict | None:
    """Invoke the pipeline. Mirrors event_loop._fire_committee but tags
    stage=paper and routes journal to logs/decisions/paper/."""
    try:
        from argos.data.ohlcv_loader import load_ohlcv
        from argos.graph import compile_graph, create_initial_state
        from argos.journal import append_record, build_record
        from argos.token_usage import start_decision
    except Exception as e:  # noqa: BLE001
        logger.error("paper: cannot import pipeline: %s", e)
        return None

    try:
        window = load_ohlcv(ticker, as_of, allow_today=True)
    except Exception as e:  # noqa: BLE001
        logger.error("paper: OHLCV load failed for %s: %s", ticker, e)
        return None

    state = create_initial_state(ticker=ticker, as_of_date=as_of, ohlcv_window=window)
    state["triggers"] = [t.to_dict() for t in triggers]  # type: ignore[typeddict-unknown-key]
    state["stage"] = "paper"                               # type: ignore[typeddict-unknown-key]

    start_decision()
    t0 = time.time()
    try:
        final = compile_graph().invoke(state)
    except Exception as e:  # noqa: BLE001
        logger.error("paper: pipeline raised on %s: %s", ticker, e)
        return None
    duration = time.time() - t0

    bars = window.get("ohlcv") or []
    last = bars[-1] if bars else {}
    record = build_record(ticker, as_of, final, {
        "rows": len(bars),
        "date_range": window.get("date_range"),
        "last_close": last.get("close"),
        "last_date": last.get("date"),
    }, duration)
    record["triggers"] = [t.to_dict() for t in triggers]
    record["stage"] = "paper"

    out_dir = PAPER_DIR / market / ticker.upper()
    path = append_record(record, out_dir, ticker, as_of)
    logger.info("  paper journal → %s", path.relative_to(REPO_ROOT))
    return record


# ── Main ───────────────────────────────────────────────────────────────────

def _scan_once(args: argparse.Namespace, pf: dict) -> None:
    as_of = _today_iso()
    tickers = _universe(args)
    if not tickers:
        logger.warning("empty universe")
        return
    logger.info("── paper scan @ %s · %d tickers · dry=%s", as_of, len(tickers), args.dry_run)

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

        if args.dry_run:
            continue
        if not args.force and _already_journalled(args.market, ticker, as_of):
            logger.info("    (skipped: paper journal exists for %s@%s)", ticker, as_of)
            continue

        record = _fire_committee(args.market, ticker, as_of, fires)
        if record is None:
            continue
        fd = record.get("final_decision") or {}
        action = fd.get("action")
        size = float(fd.get("position_size") or 0.0)
        price = (record.get("ohlcv_summary") or {}).get("last_close")
        trade = _apply_decision(pf, ticker, as_of, str(action or ""), size, price)
        if trade:
            _tape(trade)
            logger.info("    paper trade: %s %.4f NAV @ %.2f", trade["action"], trade["position_size_pct_nav"], trade["fill_price"])
    _save_portfolio(pf)


def main() -> None:
    args = _parse_args()
    pf = _load_portfolio()
    _scan_once(args, pf)
    if args.watch > 0:
        logger.info("watching every %ds — Ctrl-C to stop", args.watch)
        while True:
            try:
                time.sleep(args.watch)
                _scan_once(args, pf)
            except KeyboardInterrupt:
                logger.info("stopped by user")
                break


if __name__ == "__main__":
    main()
