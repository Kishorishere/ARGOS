"""Multi-timeframe indicator snapshots (P3).

The base `compute_snapshot(records)` looks only at daily bars. Pros use
CONFLUENCE — a bull setup on daily is much more reliable when the
weekly and monthly frames also point up. This module:

  1. Resamples daily OHLCV to weekly (Fri-close) and monthly (last-day)
  2. Computes an IndicatorSnapshot on each timeframe
  3. Returns a `MultiTimeframeSnapshot` with all three + a confluence score

The forecast_node reads confluence and boosts confidence when 3-of-3
agree; the LLM sees the higher-timeframe regime as context.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from argos.indicators.engine import compute_snapshot
from argos.indicators.schemas import IndicatorSnapshot


TimeframeName = Literal["1d", "1w", "1m"]


def resample_ohlcv(records: list[dict], timeframe: TimeframeName) -> list[dict]:
    """Convert daily OHLCV records into higher-timeframe bars.

    Weekly = Friday close (or last available bar of the week).
    Monthly = last available bar of the month.
    """
    if timeframe == "1d":
        return records
    if not records:
        return []

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    if timeframe == "1w":
        rule = "W-FRI"
    elif timeframe == "1m":
        rule = "ME"  # month-end
    else:
        return records

    resampled = df.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

    out = []
    for date, row in resampled.iterrows():
        out.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        })
    return out


def compute_multi_timeframe(records: list[dict], ticker: str, as_of_date: str,
                            timeframes: tuple[TimeframeName, ...] = ("1d", "1w", "1m"),
                            ) -> dict[str, IndicatorSnapshot]:
    """Compute IndicatorSnapshot on each requested timeframe.

    Returns {timeframe_name: snapshot}. Missing/short data → snapshot with
    bars_used < warmup (indicators mostly NaN but the object exists).
    """
    out: dict[str, IndicatorSnapshot] = {}
    for tf in timeframes:
        try:
            resampled = resample_ohlcv(records, tf)
            if not resampled:
                continue
            out[tf] = compute_snapshot(resampled, ticker, as_of_date)
        except Exception:
            # A too-short window shouldn't kill the run; return empty snap
            continue
    return out


def confluence_score(mts: dict[str, IndicatorSnapshot]) -> dict:
    """Direction agreement across timeframes.

    Returns:
        {
          "n_bullish": int,   # timeframes with net_bias > 0
          "n_bearish": int,
          "n_neutral": int,
          "aligned_bullish": bool,   # ALL timeframes bullish
          "aligned_bearish": bool,
          "score": float,   # -1..+1 signed avg of normalized biases
          "by_timeframe": {tf: {"net_bias": ..., "regime": ...}}
        }
    """
    scores = {}
    n_bull = n_bear = n_neu = 0
    normalized = []
    for tf, snap in mts.items():
        nb = snap.net_bias_score if snap and snap.bars_used > 20 else 0.0
        regime = snap.trend.regime if snap else None
        scores[tf] = {"net_bias": round(nb, 3), "regime": regime}
        if nb > 0.5:
            n_bull += 1
        elif nb < -0.5:
            n_bear += 1
        else:
            n_neu += 1
        # Normalize into [-1..1] via tanh-like clipping
        normalized.append(max(-1.0, min(1.0, nb / 5.0)))
    signed_score = sum(normalized) / max(len(normalized), 1)
    return {
        "n_bullish": n_bull,
        "n_bearish": n_bear,
        "n_neutral": n_neu,
        "aligned_bullish": (n_bull == len(mts) and n_bull > 0),
        "aligned_bearish": (n_bear == len(mts) and n_bear > 0),
        "score": round(signed_score, 3),
        "by_timeframe": scores,
    }
