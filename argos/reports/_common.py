"""Shared plumbing for the reporting modules — load journal records,
join with realized returns, compute hit rate + bootstrap CI per horizon."""

from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

JOURNAL_DIR = Path(__file__).resolve().parents[2] / "logs" / "decisions"


def load_journal_records(since: str | None = None, until: str | None = None,
                         ticker_filter: list[str] | None = None) -> list[dict]:
    """Load journal records within [since, until], optionally filtered by ticker."""
    records: list[dict] = []
    if not JOURNAL_DIR.exists():
        return records
    for jf in sorted(JOURNAL_DIR.glob("*_*.jsonl")):
        if jf.stem.endswith("combined"):
            continue
        parts = jf.stem.rsplit("_", 1)
        if len(parts) != 2 or len(parts[1]) != 10:
            continue
        symbol, d = parts
        if since and d < since:
            continue
        if until and d > until:
            continue
        if ticker_filter and symbol.upper() not in [t.upper() for t in ticker_filter]:
            continue
        for line in jf.read_text().splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
    # Dedupe: keep the latest attempt per (ticker, date)
    latest: dict[tuple[str, str], dict] = {}
    for r in records:
        key = (r.get("ticker", ""), r.get("date", ""))
        if key not in latest or r.get("attempted_at", "") > latest[key].get("attempted_at", ""):
            latest[key] = r
    return sorted(latest.values(), key=lambda r: (r.get("date", ""), r.get("ticker", "")))


def bootstrap_hit_rate_ci(hits: int, n: int, iters: int = 2000,
                          alpha: float = 0.05) -> tuple[float, float]:
    """Return (lo, hi) 95% CI on hit-rate proportion via bootstrap."""
    if n == 0:
        return (0.0, 0.0)
    random.seed(42)
    trials = []
    p = hits / n
    for _ in range(iters):
        sample = [1 if random.random() < p else 0 for _ in range(n)]
        trials.append(sum(sample) / n)
    trials.sort()
    lo = trials[int(alpha / 2 * iters)]
    hi = trials[int((1 - alpha / 2) * iters)]
    return (lo, hi)


def realized_return_at(ticker: str, as_of: str, horizon_days: int,
                       price_provider) -> float | None:
    """Return the % move from as_of close to as_of + horizon_days close.

    price_provider is a callable(symbol, as_of, lookback_days) → dict with 'ohlcv' key.
    """
    try:
        data = price_provider(ticker, as_of, lookback_days=horizon_days + 60)
    except Exception:
        return None
    records = data.get("ohlcv") or []
    dates = [r["date"] for r in records]
    closes = {r["date"]: r["close"] for r in records}
    if as_of not in dates:
        return None
    idx = dates.index(as_of)
    if idx + horizon_days >= len(dates):
        return None
    p_now = closes[as_of]
    p_future = closes[dates[idx + horizon_days]]
    if not p_now:
        return None
    return (p_future / p_now - 1) * 100


def score_horizon(records: list[dict], horizon_days: int, horizon_key: str,
                   price_provider) -> dict:
    """For each record with a multi-horizon forecast, join with realized
    return at horizon; compute hit rate + mean forecast error.
    """
    hits = 0
    scored = 0
    forecast_errors: list[float] = []
    misses: list[dict] = []
    for r in records:
        mhf = r.get("multi_horizon_forecast")
        if not mhf:
            continue
        hz = (mhf.get("horizons") or {}).get(horizon_key)
        if not hz:
            continue
        realized = realized_return_at(r["ticker"], r["date"], horizon_days, price_provider)
        if realized is None:
            continue
        scored += 1
        p_up = hz.get("probability_up") or 0.5
        if abs(p_up - 0.5) < 0.05:
            continue  # skip "flat" calls
        pred_dir = "up" if p_up > 0.5 else "down"
        real_dir = "up" if realized > 0 else "down"
        is_hit = (pred_dir == real_dir)
        if is_hit:
            hits += 1
        else:
            misses.append({"ticker": r["ticker"], "date": r["date"],
                           "pred": pred_dir, "realized": realized})
        exp = hz.get("expected_return_pct") or 0.0
        forecast_errors.append(realized - exp)
    n = scored
    hit_rate = hits / n if n else 0.0
    lo, hi = bootstrap_hit_rate_ci(hits, n)
    mean_err = sum(forecast_errors) / len(forecast_errors) if forecast_errors else 0.0
    return {
        "horizon": horizon_key,
        "n": n,
        "hits": hits,
        "hit_rate_pct": round(100 * hit_rate, 2),
        "ci_95_pct": (round(100 * lo, 2), round(100 * hi, 2)),
        "mean_forecast_error_pct": round(mean_err, 3),
        "top_misses": misses[:5],
    }
