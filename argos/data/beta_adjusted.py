"""Beta-adjusted returns vs NEPSE (P28).

A stock up 2% on a day when NEPSE is up 2% has zero idiosyncratic alpha.
Same stock up 2% when NEPSE is FLAT is a real signal of stock-specific
demand. This module estimates a rolling beta of each symbol vs the NEPSE
index and returns the residual (alpha) component of today's move.

Uses:
  - Closes from ohlcv_records (already loaded upstream)
  - NEPSE index closes from data/sector_history.jsonl (populated daily)

Signal:
  `beta_adjusted_return_signal(symbol, ohlcv_records, lookback=60)`
    → residual return (today's alpha) normalized by rolling stdev.
    +1.0 = today's alpha is 1σ above zero (stock outperformed beta prediction)
    -1.0 = 1σ below
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from pathlib import Path

logger = logging.getLogger(__name__)

SECTOR_HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "sector_history.jsonl"


def _nepse_index_history() -> list[tuple[str, float]]:
    """Return sorted (date, nepse_index) tuples from sector_history.jsonl."""
    if not SECTOR_HISTORY_PATH.exists():
        return []
    rows = []
    for line in SECTOR_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        for s in e.get("sectors", []):
            if s.get("name") == "NEPSE Index" and s.get("index"):
                rows.append((e["date"], float(s["index"])))
                break
    rows.sort(key=lambda r: r[0])
    return rows


def _daily_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev > 0:
            out.append(closes[i] / prev - 1)
    return out


def rolling_beta(stock_closes: list[float], index_closes: list[float]) -> float | None:
    """OLS beta via cov/var, no numpy dependency required."""
    n = min(len(stock_closes), len(index_closes))
    if n < 20:
        return None
    s_ret = _daily_returns(stock_closes[-n:])
    i_ret = _daily_returns(index_closes[-n:])
    m = min(len(s_ret), len(i_ret))
    if m < 15:
        return None
    s_ret = s_ret[-m:]
    i_ret = i_ret[-m:]
    mean_s = sum(s_ret) / m
    mean_i = sum(i_ret) / m
    cov = sum((s_ret[k] - mean_s) * (i_ret[k] - mean_i) for k in range(m)) / m
    var_i = sum((i_ret[k] - mean_i) ** 2 for k in range(m)) / m
    if var_i <= 0:
        return None
    return cov / var_i


def beta_adjusted_return_signal(symbol: str, ohlcv_records: list[dict],
                                lookback: int = 60) -> float | None:
    """Return today's ALPHA (idiosyncratic return) normalized by rolling
    residual stdev, in [-1, +1] via tanh.

    Nil if we lack enough NEPSE-index history (needs ~30 days accumulated
    via `snapshot_sectors_today`).
    """
    if len(ohlcv_records) < lookback:
        return None
    idx_hist = _nepse_index_history()
    if len(idx_hist) < 20:
        return None
    stock_closes = [r.get("close") or 0 for r in ohlcv_records[-lookback:]]
    stock_dates = [r.get("date") for r in ohlcv_records[-lookback:]]
    # Align on the same set of dates present in both series
    idx_by_date = dict(idx_hist)
    paired = [(d, c) for d, c in zip(stock_dates, stock_closes) if d in idx_by_date]
    if len(paired) < 20:
        return None
    aligned_stock = [c for _, c in paired]
    aligned_idx = [idx_by_date[d] for d, _ in paired]
    beta = rolling_beta(aligned_stock, aligned_idx)
    if beta is None:
        return None
    s_ret = _daily_returns(aligned_stock)
    i_ret = _daily_returns(aligned_idx)
    m = min(len(s_ret), len(i_ret))
    if m < 15:
        return None
    residuals = [s_ret[-m + k] - beta * i_ret[-m + k] for k in range(m)]
    today_residual = residuals[-1]
    try:
        sigma = statistics.pstdev(residuals[:-1])
    except statistics.StatisticsError:
        return None
    if sigma <= 0:
        return None
    z = today_residual / sigma
    # tanh normalization → smooth [-1, +1] with saturation at ~|z| = 2
    return round(math.tanh(z / 2), 3)


def beta_of(symbol: str, ohlcv_records: list[dict], lookback: int = 60) -> float | None:
    """Direct beta accessor — helpful for report cards / debugging."""
    if len(ohlcv_records) < 20:
        return None
    idx_hist = _nepse_index_history()
    if len(idx_hist) < 20:
        return None
    stock_closes = [r.get("close") or 0 for r in ohlcv_records[-lookback:]]
    stock_dates = [r.get("date") for r in ohlcv_records[-lookback:]]
    idx_by_date = dict(idx_hist)
    paired = [(d, c) for d, c in zip(stock_dates, stock_closes) if d in idx_by_date]
    if len(paired) < 20:
        return None
    return rolling_beta([c for _, c in paired], [idx_by_date[d] for d, _ in paired])
