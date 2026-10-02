"""Cross-sectional universe ranker.

Instead of asking "should I buy NABIL this week", ask "which 3 of my 11
cached tickers are the best hold for the next month". This is the same
shape as the sibling `nepse-quant-terminal`'s primary edge (cross-sectional
signals) and is where the alpha actually lives at this scale.

Two modes:

    python scripts/rank_universe.py
        Rank every cached ticker by (expected_return_pct * probability_up)
        on the 1-month horizon. Prints the top 3 with prices, targets,
        drivers, and the machine's confidence.

    python scripts/rank_universe.py --audit 12
        Walk-forward 12 monthly checkpoints. At each checkpoint, take the
        machine's top 3 picks (equal weight) and compare their 21-bar
        forward return against the equal-weight full-universe return.
        This is the primary success metric for the plan: top-3 must beat
        equal-weight by >=3% cumulative over 12 months.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv
load_dotenv(REPO / ".env", override=True)

from argos.data.nepse_ohlcv import load_nepse_ohlcv
from scripts.one_week_forecast import _forecast_from_records, _yesterday_str  # noqa: E402

CACHE_DIR = REPO / "data" / "cache" / "nepse_ohlcv"


def _cached_tickers() -> list[str]:
    return sorted(p.stem for p in CACHE_DIR.glob("*.pkl"))


@dataclass
class Pick:
    ticker: str
    last_close: float
    exp_pct: float
    p_up: float
    conf: float
    score: float          # expected_return_pct * probability_up
    drivers: list[str]

    @property
    def target(self) -> float:
        return self.last_close * (1 + self.exp_pct / 100)


def _rank_at(as_of_date: str, horizon: str = "1m") -> list[Pick]:
    picks: list[Pick] = []
    for ticker in _cached_tickers():
        try:
            data = load_nepse_ohlcv(ticker, as_of_date, lookback_days=400)
        except Exception:
            continue
        records = data["ohlcv"]
        if len(records) < 60:
            continue
        try:
            mhf = _forecast_from_records(ticker, records, as_of_date)
        except Exception:
            continue
        h = (mhf.get("horizons") or {}).get(horizon) or {}
        if not h:
            continue
        exp = h.get("expected_return_pct", 0.0)
        p_up = h.get("probability_up", 0.5)
        conf = h.get("confidence", 0.0)
        picks.append(Pick(
            ticker=ticker,
            last_close=records[-1]["close"],
            exp_pct=exp,
            p_up=p_up,
            conf=conf,
            score=_long_score(exp, p_up),
            drivers=(h.get("key_drivers") or [])[:3],
        ))
    picks.sort(key=lambda x: x.score, reverse=True)
    return picks


def _long_score(exp_pct: float, p_up: float) -> float:
    """Long-only ranking score.

    Uses `expected_return_pct` directly as the score, subject to a soft
    gate on p_up. Rationale: after the isotonic calibrator (item 7b),
    p_up values across tickers get squashed toward the base rate (~0.42),
    which destroys ranking signal if we multiply by (2p-1). The terminal's
    cross-sectional edge lives in the MAGNITUDE ordering, not per-ticker
    calibrated confidence. So: rank by expected, but penalize
    contradictions (p_up on the wrong side of 0.5 given the sign of exp).
    """
    if exp_pct > 0 and p_up < 0.5:
        return exp_pct * (2 * p_up - 1)   # contradiction — sink it
    if exp_pct < 0 and p_up > 0.5:
        return exp_pct * (2 * p_up - 1)   # contradiction — sink it
    return exp_pct


def _print_ranking(as_of_date: str, picks: list[Pick]) -> None:
    if not picks:
        print("No pickable tickers.")
        return
    print()
    print(f"=== Universe ranking as of {as_of_date} (1-month horizon) ===")
    print(f"{'#':>2}  {'ticker':6s}  {'close':>9s}  {'target':>9s}  "
          f"{'exp%':>7s}  {'p_up':>5s}  {'conf':>5s}  {'score':>7s}")
    for i, p in enumerate(picks, 1):
        marker = ">>>" if i <= 3 else "   "
        print(f"{marker[:2]}{i:>1}  {p.ticker:6s}  {p.last_close:9.2f}  {p.target:9.2f}  "
              f"{p.exp_pct:+7.2f}  {p.p_up:5.2f}  {p.conf:5.2f}  {p.score:+7.2f}")
    print()
    print("Top 3 picks (equal-weight hold, ~40 trading days):")
    for i, p in enumerate(picks[:3], 1):
        drivers = ", ".join(p.drivers) if p.drivers else "-"
        print(f"  {i}. {p.ticker}  NPR {p.last_close:,.2f} -> {p.target:,.2f}  "
              f"({p.exp_pct:+.1f}%, p_up {p.p_up:.0%})")
        print(f"     drivers: {drivers}")
    print()


# ── Audit mode ──────────────────────────────────────────────────────────────

def _load_all_records(as_of_date: str) -> dict[str, list[dict]]:
    """Load full available history for every cached ticker up to as_of_date."""
    out: dict[str, list[dict]] = {}
    for ticker in _cached_tickers():
        try:
            data = load_nepse_ohlcv(ticker, as_of_date, lookback_days=2000)
        except Exception:
            continue
        out[ticker] = data["ohlcv"]
    return out


def _rank_from_records(records_by_ticker: dict[str, list[dict]],
                       as_of_date: str, horizon: str = "1m") -> list[Pick]:
    """In-memory ranking — used by audit to avoid re-loading caches every step."""
    picks: list[Pick] = []
    for ticker, records in records_by_ticker.items():
        # Slice to as_of_date (records are sorted)
        cutoff = as_of_date
        slice_ = [r for r in records if r["date"] <= cutoff]
        if len(slice_) < 60:
            continue
        try:
            mhf = _forecast_from_records(ticker, slice_, as_of_date)
        except Exception:
            continue
        h = (mhf.get("horizons") or {}).get(horizon) or {}
        if not h:
            continue
        exp_pct = h.get("expected_return_pct", 0.0)
        p_up = h.get("probability_up", 0.5)
        picks.append(Pick(
            ticker=ticker,
            last_close=slice_[-1]["close"],
            exp_pct=exp_pct,
            p_up=p_up,
            conf=h.get("confidence", 0.0),
            score=_long_score(exp_pct, p_up),
            drivers=(h.get("key_drivers") or [])[:3],
        ))
    picks.sort(key=lambda x: x.score, reverse=True)
    return picks


def _realized_return_pct(records: list[dict], as_of_date: str, bars_forward: int) -> float | None:
    """Return the close-to-close % return `bars_forward` trading days after as_of_date.

    Returns None if we don't have that far ahead.
    """
    idx = next((i for i, r in enumerate(records) if r["date"] == as_of_date), -1)
    if idx < 0 or idx + bars_forward >= len(records):
        return None
    entry = records[idx]["close"]
    exit_ = records[idx + bars_forward]["close"]
    if entry == 0:
        return None
    return (exit_ / entry - 1) * 100


def _audit(months: int, verbose: bool = False, horizon: str = "1m",
           bars_forward: int = 21, top_n: int = 3) -> None:
    """Walk-forward audit: top-N picks vs equal-weight universe."""
    all_records = _load_all_records(_yesterday_str())
    if not all_records:
        print("No cached tickers found.", file=sys.stderr)
        sys.exit(1)

    # Anchor on the ticker with the longest history so we get the widest
    # checkpoint set possible.
    anchor_ticker = max(all_records, key=lambda k: len(all_records[k]))
    anchor_records = all_records[anchor_ticker]
    # Bar-index step ~ 21 (one month). Leave `bars_forward` bars at the tail.
    last_idx = len(anchor_records) - bars_forward - 1
    step = 21
    start_idx = max(200, last_idx - months * step)
    checkpoints_idx = list(range(start_idx, last_idx + 1, step))
    if not checkpoints_idx:
        print("Not enough history to run audit.", file=sys.stderr)
        sys.exit(1)

    if verbose:
        print(f"{'date':10s}  top3 picks              top3%    universe%   delta")

    top_rets: list[float] = []
    univ_rets: list[float] = []
    for idx in checkpoints_idx:
        as_of = anchor_records[idx]["date"]
        picks = _rank_from_records(all_records, as_of, horizon=horizon)
        if len(picks) < top_n:
            continue

        # Top-N realized (equal weight)
        top_rs: list[float] = []
        for p in picks[:top_n]:
            r = _realized_return_pct(all_records[p.ticker], as_of, bars_forward)
            if r is not None:
                top_rs.append(r)
        # Universe realized (equal weight across ALL tickers we had)
        univ_rs: list[float] = []
        for tk, rec in all_records.items():
            r = _realized_return_pct(rec, as_of, bars_forward)
            if r is not None:
                univ_rs.append(r)
        if not top_rs or not univ_rs:
            continue
        top_ret = statistics.mean(top_rs)
        univ_ret = statistics.mean(univ_rs)
        top_rets.append(top_ret)
        univ_rets.append(univ_ret)

        if verbose:
            names = "/".join(p.ticker for p in picks[:top_n])
            print(f"{as_of}  {names:22s} {top_ret:+7.2f}%  {univ_ret:+7.2f}%  "
                  f"{top_ret - univ_ret:+7.2f}%")

    if not top_rets:
        print("Audit ran but produced no comparable checkpoints.", file=sys.stderr)
        sys.exit(1)

    top_cum = sum(top_rets)
    univ_cum = sum(univ_rets)
    top_avg = statistics.mean(top_rets)
    univ_avg = statistics.mean(univ_rets)
    beats = sum(1 for t, u in zip(top_rets, univ_rets) if t > u)

    print()
    print(f"=== Cross-sectional audit: {len(top_rets)} monthly checkpoints ===")
    print(f"Top-{top_n} equal-weight avg %/mo ..... {top_avg:+.2f}%")
    print(f"Universe equal-weight avg %/mo .. {univ_avg:+.2f}%")
    print(f"Delta ........................... {top_avg - univ_avg:+.2f}% per month")
    print(f"Cumulative delta ................ {top_cum - univ_cum:+.2f}% "
          f"over {len(top_rets)} months")
    print(f"Checkpoints top-{top_n} beat universe .. {beats}/{len(top_rets)} = "
          f"{100 * beats / len(top_rets):.1f}%")
    print()
    if top_cum - univ_cum >= 3.0:
        print(">>> PASS: cross-sectional strategy beats equal-weight by >=3% cumulative.")
    else:
        print(">>> FAIL: cross-sectional edge <3% cumulative; ranker not yet useful.")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Cross-sectional universe ranker (NEPSE)."
    )
    p.add_argument("--as-of", default=None,
                   help="Anchor date YYYY-MM-DD (default: last cached trading day)")
    p.add_argument("--audit", type=int, default=0, metavar="N",
                   help="Walk-forward N monthly checkpoints, compare top-3 vs universe")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Per-checkpoint audit rows")
    p.add_argument("--top", type=int, default=3, help="Top-N cross-sectional picks (default 3)")
    args = p.parse_args()

    if args.audit > 0:
        _audit(args.audit, verbose=args.verbose, top_n=args.top)
        return

    as_of = args.as_of or _yesterday_str()
    picks = _rank_at(as_of)
    _print_ranking(as_of, picks)


if __name__ == "__main__":
    main()
