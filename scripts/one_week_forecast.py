"""One-week directional forecast for a single NEPSE ticker.

Wraps the deterministic `forecast_node` (no LLM, no quota) around cached
NEPSE OHLCV so you can ask "given today's close, where does the machine
think NABIL will be five trading days from now, and should I buy?"

Two modes:

    python scripts/one_week_forecast.py NABIL
        Forecast from the most recent cached bar. Prints today's close,
        the 1-week target price, the p10/p90 price band, direction
        confidence, and a BUY / WATCH / AVOID call.

    python scripts/one_week_forecast.py NABIL --audit 30
        Walk-forward directional audit. Rebuilds the same forecast for
        the last 30 weekly checkpoints, compares against the realized
        close five trading days later, and reports hit rate. This is the
        number that answers "how much should I trust the arrow?"

Directional accuracy is treated as the primary score. Magnitude MAE is
reported too but never used to gate a trade.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Load .env so KRONOS_ENDPOINT_URL etc. are respected if set.
from dotenv import load_dotenv
load_dotenv(REPO / ".env", override=True)

from argos.data.nepse_ohlcv import load_nepse_ohlcv
from argos.indicators import compute_snapshot
from argos.indicators.multi_timeframe import compute_multi_timeframe, confluence_score
from argos.nodes.forecast import forecast_node


# Nepal trading week: Sunday..Thursday (weekday 6, 0, 1, 2, 3).
NEPAL_TRADING_DAYS = {6, 0, 1, 2, 3}


def _next_n_trading_days(from_date: datetime, n: int) -> datetime:
    cur, count = from_date, 0
    while count < n:
        cur += timedelta(days=1)
        if cur.weekday() in NEPAL_TRADING_DAYS:
            count += 1
    return cur


def _forecast_from_records(ticker: str, records: list[dict], as_of_date: str) -> dict:
    """Run the deterministic multi-horizon forecast on a records slice."""
    snap = compute_snapshot(records, ticker=ticker, as_of_date=as_of_date)
    mts = compute_multi_timeframe(records, ticker=ticker, as_of_date=as_of_date)
    conf = confluence_score(mts)
    snap_dict = snap.model_dump()
    snap_dict["multi_timeframe"] = {tf: s.model_dump() for tf, s in mts.items()}
    snap_dict["confluence"] = conf
    state = {
        "ticker": ticker,
        "as_of_date": as_of_date,
        "technicals_signal": {"indicator_snapshot": snap_dict},
        "kronos_forecast": None,
        "ohlcv_window": {"ohlcv": records},
    }
    return forecast_node(state).get("multi_horizon_forecast") or {}


def _decision(direction: str, p_up: float, confidence: float,
              expected_pct: float, breakeven_pct: float) -> tuple[str, str]:
    """Map the 1w forecast to a Nepal-realistic action.

    BUY needs: direction up, p_up >= 0.65, confidence >= 0.65, AND the
    expected return must clear the ~1.0% round-trip fee twice (buffer).
    """
    if direction == "up" and p_up >= 0.65 and confidence >= 0.65 \
            and expected_pct >= 2 * breakeven_pct:
        return ("BUY", f"p_up {p_up:.0%}, conf {confidence:.0%}, expected {expected_pct:+.1f}% "
                       f"clears {2 * breakeven_pct:.1f}% breakeven buffer.")
    if direction == "down" and p_up <= 0.35 and confidence >= 0.65:
        return ("AVOID", f"p_up {p_up:.0%}, conf {confidence:.0%}, expected {expected_pct:+.1f}% — "
                         "wait or exit if long.")
    return ("WATCH", f"Not confident enough (p_up {p_up:.0%}, conf {confidence:.0%}, "
                     f"expected {expected_pct:+.1f}%). Skip — no edge over fees.")


def _print_forecast(ticker: str, as_of_date: str, mhf: dict) -> None:
    horizons = mhf.get("horizons", {})
    h1w = horizons.get("1w") or {}
    last_close = mhf.get("last_close") or 0.0
    exp = h1w.get("expected_return_pct", 0.0)
    p10 = h1w.get("p10_return_pct", 0.0)
    p90 = h1w.get("p90_return_pct", 0.0)
    direction = h1w.get("direction", "flat")
    p_up = h1w.get("probability_up", 0.5)
    conf = h1w.get("confidence", 0.0)

    target = last_close * (1 + exp / 100)
    low = last_close * (1 + p10 / 100)
    high = last_close * (1 + p90 / 100)
    target_date = _next_n_trading_days(datetime.strptime(as_of_date, "%Y-%m-%d"), 5)

    # Nepal round-trip cost: 2× (0.40 + 0.015)% ≈ 0.83% (see argos/simulate.py).
    BREAKEVEN = 0.83
    action, why = _decision(direction, p_up, conf, exp, BREAKEVEN)

    print()
    print(f"=== {ticker} -- 1-week forecast (as of {as_of_date}) ===")
    print(f"Today's close ....... NPR {last_close:,.2f}")
    print(f"Target (5 biz days) . NPR {target:,.2f}  ({exp:+.2f}%)  -> around {target_date.date()}")
    print(f"Band (p10..p90) ..... NPR {low:,.2f} .. NPR {high:,.2f}  ({p10:+.1f}% .. {p90:+.1f}%)")
    print(f"Direction ........... {direction.upper()}   p_up = {p_up:.0%}   confidence = {conf:.0%}")
    print(f"Round-trip fees ..... {2*BREAKEVEN:.2f}% (Nepal broker + SEBON, both sides)")
    print()
    print(f">>> {action}")
    print(f"    {why}")
    drivers = h1w.get("key_drivers") or []
    if drivers:
        print(f"    Drivers: {', '.join(drivers[:5])}")
    print()

    # Also surface 1d / 1m for context — sometimes 1w is at odds with 1m.
    for key in ("1d", "1m"):
        hh = horizons.get(key) or {}
        if not hh:
            continue
        print(f"   [{key}] {hh.get('direction','?').upper():5s} "
              f"p_up={hh.get('probability_up',0):.0%} "
              f"exp={hh.get('expected_return_pct',0):+.2f}% "
              f"conf={hh.get('confidence',0):.0%}")
    print()


# ── Audit mode ──────────────────────────────────────────────────────────────

def _audit(ticker: str, weeks: int, verbose: bool = False) -> None:
    """Walk-forward directional accuracy on the last N weekly checkpoints.

    For each checkpoint date, we take only the bars up to that date, run
    the forecast, then look 5 trading days ahead and check whether the
    machine's direction matches the realized close-to-close move.

    Reports hit rate overall AND at high-confidence subset (the trades
    you'd actually take under the BUY/AVOID rule).
    """
    # Pull the full cached history once.
    data = load_nepse_ohlcv(ticker, _yesterday_str(), lookback_days=2000)
    records = data["ohlcv"]
    if len(records) < 220:
        print(f"Not enough history for {ticker}: {len(records)} bars (need ≥220).", file=sys.stderr)
        sys.exit(1)

    # Weekly checkpoints spaced 5 trading days apart, ending 5 bars before
    # the last cached bar so we always have a realized outcome to compare.
    last_idx = len(records) - 6
    checkpoints = list(range(max(200, last_idx - weeks * 5), last_idx + 1, 5))
    if not checkpoints:
        print("No usable checkpoints — dataset too short.", file=sys.stderr)
        sys.exit(1)

    total = 0
    hits = 0
    high_conf_total = 0
    high_conf_hits = 0
    signed_pct_error = 0.0
    trades_taken = 0
    trades_hit = 0
    trades_pnl_pct = 0.0
    BREAKEVEN = 0.83

    if verbose:
        print(f"{'date':10s}  {'close':>9s}  {'dir':4s}  {'p_up':>5s} "
              f"{'exp%':>6s}  {'real%':>6s}  {'hit':>3s}")

    for idx in checkpoints:
        as_of = records[idx]["date"]
        slice_ = records[: idx + 1]
        try:
            mhf = _forecast_from_records(ticker, slice_, as_of)
        except Exception as e:
            if verbose:
                print(f"{as_of}  forecast failed: {e}")
            continue
        h1w = (mhf.get("horizons") or {}).get("1w") or {}
        if not h1w:
            continue
        direction = h1w.get("direction", "flat")
        p_up = h1w.get("probability_up", 0.5)
        confidence = h1w.get("confidence", 0.0)
        exp = h1w.get("expected_return_pct", 0.0)

        entry = records[idx]["close"]
        exit_ = records[idx + 5]["close"]
        real_pct = (exit_ / entry - 1) * 100
        real_dir = "up" if real_pct > 0 else ("down" if real_pct < 0 else "flat")

        total += 1
        # Directional hit: skip "flat" forecast (no view)
        if direction != "flat":
            hit = (direction == real_dir)
            if hit:
                hits += 1
            if confidence >= 0.65 and p_up >= 0.65 or p_up <= 0.35:
                high_conf_total += 1
                if hit:
                    high_conf_hits += 1
            # If our BUY/AVOID rule would have fired: score the round-trip.
            action, _ = _decision(direction, p_up, confidence, exp, BREAKEVEN)
            if action == "BUY":
                trades_taken += 1
                trades_pnl_pct += real_pct - 2 * BREAKEVEN
                if real_pct > 2 * BREAKEVEN:
                    trades_hit += 1
        else:
            hit = None

        signed_pct_error += abs(exp - real_pct)

        if verbose:
            hit_mark = "OK" if hit else ("--" if hit is False else " .")
            print(f"{as_of}  {entry:9.2f}  {direction:4s}  {p_up:5.2f} "
                  f"{exp:+6.2f}  {real_pct:+6.2f}  {hit_mark:>3s}")

    if total == 0:
        print("No comparable checkpoints — nothing to score.", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"=== Walk-forward audit: {ticker} -- {total} weekly checkpoints ===")
    print(f"Directional hit rate (non-flat): {hits}/{total} = {100*hits/total:.1f}%   "
          f"(coin flip = 50%)")
    if high_conf_total:
        print(f"High-confidence subset .........: {high_conf_hits}/{high_conf_total} = "
              f"{100*high_conf_hits/high_conf_total:.1f}%")
    print(f"Mean absolute magnitude error ..: {signed_pct_error/total:.2f}%  "
          f"(informational only)")
    if trades_taken:
        print()
        print(f"Simulated BUY trades under the current rule:")
        print(f"  taken ............ {trades_taken}")
        print(f"  net-of-fee wins .. {trades_hit}/{trades_taken} = "
              f"{100*trades_hit/trades_taken:.1f}%")
        print(f"  avg net %/trade .. {trades_pnl_pct/trades_taken:+.2f}%")
        print(f"  total net % ...... {trades_pnl_pct:+.2f}%")
    else:
        print()
        print("BUY rule never fired on this window — the rule is too strict, "
              "or this ticker's regime doesn't clear the confidence bar.")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────

def _yesterday_str() -> str:
    """Return the last strictly-past business day as YYYY-MM-DD (loader is
    hard-coded to reject as_of >= today)."""
    d = datetime.now() - timedelta(days=1)
    while d.weekday() not in NEPAL_TRADING_DAYS:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def main() -> None:
    p = argparse.ArgumentParser(
        description="One-week directional forecast for a NEPSE ticker."
    )
    p.add_argument("ticker", help="NEPSE symbol, e.g. NABIL, ADBL, NIFRA")
    p.add_argument("--as-of", default=None,
                   help="Anchor date YYYY-MM-DD (default: last cached trading day)")
    p.add_argument("--audit", type=int, default=0, metavar="N",
                   help="Walk-forward N weekly checkpoints and report directional hit rate")
    p.add_argument("-v", "--verbose", action="store_true", help="Per-checkpoint audit rows")
    args = p.parse_args()

    ticker = args.ticker.upper()
    as_of = args.as_of or _yesterday_str()

    if args.audit > 0:
        _audit(ticker, args.audit, verbose=args.verbose)
        return

    data = load_nepse_ohlcv(ticker, as_of, lookback_days=400)
    mhf = _forecast_from_records(ticker, data["ohlcv"], as_of)
    if not mhf:
        print(f"Forecast unavailable for {ticker} @ {as_of} (missing indicators).",
              file=sys.stderr)
        sys.exit(2)
    _print_forecast(ticker, as_of, mhf)


if __name__ == "__main__":
    main()
