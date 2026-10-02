"""Event-conditional alerts CLI — the "80% oracle" consumer surface.

Fires signals ONLY when a hard event was detected in the last N bars for
a symbol. Silent otherwise. When it fires, it prints the tier
(silent / BUY / STRONG BUY), the calibrated 2-week direction probability,
the event that anchored the call, and the raw drivers.

Two modes:

    python scripts/event_alerts.py
        Live-mode: iterate the 11 cached tickers, check for events in
        the last 5 trading days, run the calibrated event classifier on
        each, print a table sorted by calibrated p_up.

    python scripts/event_alerts.py --audit 12
        Walk-forward N months: for every historical event, score the
        classifier as it would have run in real time, compare against
        the realized 10-bar close-to-close direction, report per-tier
        hit rates and coverage.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv
load_dotenv(REPO / ".env", override=True)

from argos.data.nepse_ohlcv import load_nepse_ohlcv
from argos.data.universe import cached_symbols
from argos.events import events_in_last, load_events
from argos.learn.calibration import calibrate_event, event_tier
from argos.learn.event_conditional import predict_event

NEPAL_TRADING_DAYS = {6, 0, 1, 2, 3}


def _yesterday() -> str:
    d = datetime.now() - timedelta(days=1)
    while d.weekday() not in NEPAL_TRADING_DAYS:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ── Live mode ──────────────────────────────────────────────────────────────

def _live(freshness_bars: int = 5) -> None:
    as_of = _yesterday()
    print(f"=== Event alerts as of {as_of} (events within {freshness_bars} days) ===")
    print(f"{'ticker':6s}  {'event':16s}  {'event_dt':10s}  "
          f"{'p_raw':>6s}  {'p_cal':>6s}  {'tier':>10s}  driver")
    fired = 0
    for ticker in cached_symbols():
        try:
            events = events_in_last(ticker, as_of, freshness_bars,
                                    include_broker_cluster=True)
        except Exception:
            events = []
        if not events:
            continue
        # For each event, score the LATEST bar (as_of) with that event
        # attached. If model isn't trained, predict_event returns None.
        for ev in events:
            p_raw = predict_event(ticker, ev, as_of)
            if p_raw is None:
                continue
            p_cal = calibrate_event(p_raw)
            tier = event_tier(p_cal)
            if tier == "silent":
                continue
            title = (ev.raw or {}).get("title") or ev.event_type
            print(f"{ticker:6s}  {ev.event_type:16s}  {ev.event_date:10s}  "
                  f"{p_raw:>6.3f}  {p_cal:>6.3f}  {tier:>10s}  "
                  f"{title[:60]}")
            fired += 1
    if fired == 0:
        print()
        print("(no live event alerts today — this is EXPECTED most days)")
        print("The event overlay is designed to fire 30-80 times/year across all 11 tickers.")


# ── Audit mode ─────────────────────────────────────────────────────────────

def _audit(months: int, verbose: bool = False) -> None:
    """Walk historical events. For each, score the classifier and record
    the realized 10-bar directional outcome. Bucket by tier and event
    type. Prints coverage + hit rate per tier.
    """
    as_of = _yesterday()
    since = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    # Load events across the whole universe over the window
    events = load_events(since_date=since, include_broker_cluster=False)
    if not events:
        print("No events in the requested window — is the announcements/floorsheet "
              "cache seeded? (Run `python scripts/daily_ingest.py` daily to build up history.)")
        return

    # Pre-load OHLCV per unique symbol
    by_sym: dict[str, list[dict]] = {}
    for ev in events:
        if ev.symbol not in by_sym:
            try:
                data = load_nepse_ohlcv(ev.symbol, as_of, lookback_days=2000)
                by_sym[ev.symbol] = data["ohlcv"]
            except Exception:
                by_sym[ev.symbol] = []

    tiers = {"silent": [], "BUY": [], "STRONG BUY": []}
    by_type: dict[str, list[tuple[str, int]]] = {}
    total_events = 0
    scored_events = 0

    for ev in events:
        recs = by_sym.get(ev.symbol) or []
        if not recs:
            continue
        date_idx = {r["date"]: i for i, r in enumerate(recs)}
        idx = date_idx.get(ev.event_date)
        if idx is None:
            continue
        if idx + 10 >= len(recs):    # need forward realized
            continue
        total_events += 1
        p_raw = predict_event(ev.symbol, ev, ev.event_date)
        if p_raw is None:
            continue
        p_cal = calibrate_event(p_raw)
        tier = event_tier(p_cal)
        entry, exit_ = recs[idx]["close"], recs[idx + 10]["close"]
        y = 1 if exit_ > entry else 0
        tiers[tier].append((ev.event_type, y))
        by_type.setdefault(ev.event_type, []).append((tier, y))
        scored_events += 1

        if verbose:
            print(f"{ev.event_date}  {ev.symbol:6s}  {ev.event_type:12s}  "
                  f"p_raw={p_raw:.2f}  p_cal={p_cal:.2f}  {tier:>10s}  "
                  f"y={y}  ret={(exit_/entry-1)*100:+.2f}%")

    print()
    print(f"=== Event audit: {months}m window, {total_events} events, "
          f"{scored_events} scored ===")
    for tier_name in ("STRONG BUY", "BUY", "silent"):
        rows = tiers[tier_name]
        if not rows:
            print(f"  {tier_name:>10s}: 0 fires")
            continue
        hit = statistics.mean(y for _, y in rows)
        print(f"  {tier_name:>10s}: {len(rows):>3d} fires  hit_rate={hit:.3f}  "
              f"({months}m coverage)")
    print()
    print("Per-event-type:")
    for et, rows in sorted(by_type.items()):
        hit = statistics.mean(y for _, y in rows)
        buy_or_strong = sum(1 for t, _ in rows if t in ("BUY", "STRONG BUY"))
        print(f"  {et:16s}  n={len(rows):>3d}  overall_hit={hit:.3f}  "
              f"buy_or_strong={buy_or_strong}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Event-conditional 2-week directional alerts.")
    p.add_argument("--audit", type=int, default=0, metavar="MONTHS",
                   help="Walk-forward historical audit over N months")
    p.add_argument("--freshness", type=int, default=5,
                   help="Live-mode: only fire on events within N days")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Per-event audit rows")
    args = p.parse_args()

    if args.audit > 0:
        _audit(args.audit, verbose=args.verbose)
    else:
        _live(args.freshness)


if __name__ == "__main__":
    main()
