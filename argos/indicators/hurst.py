"""Hurst exponent — mean-reversion vs momentum regime detector (P13).

The Hurst exponent H tells you whether a series is trending (H>0.5),
mean-reverting (H<0.5), or a random walk (H~0.5). It's the classic quant
way to know WHICH kind of strategy to run:

  H > 0.55  → momentum regime: use trend-following signals (MACD, ADX, breakouts)
  H < 0.45  → mean-reversion regime: use RSI, Stoch, BB-touch fades
  0.45..55  → random walk: don't trade this stock; too noisy

Computed via rescaled range (R/S) analysis over the last N bars —
robust to non-stationarity and doesn't need a distributional assumption
like GARCH does.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_LOOKBACK = 60          # bars of history
MIN_LAG = 2
MAX_LAG_FRAC = 0.5             # cap max lag at half the window (statistical stability)


def hurst_exponent(close: pd.Series, lookback: int = DEFAULT_LOOKBACK) -> float | None:
    """Estimate the Hurst exponent H via rescaled-range analysis on the last
    `lookback` bars. Returns None if we don't have enough data.

    Implementation: for a range of lags τ ∈ [2..lookback//2], compute
    R(τ)/S(τ) then fit log(R/S) = H * log(τ) + c. Slope is H.
    """
    if len(close) < lookback + 1:
        return None
    series = close.tail(lookback).dropna().to_numpy()
    if len(series) < lookback:
        return None

    log_returns = np.diff(np.log(series))
    max_lag = max(MIN_LAG + 1, int(len(log_returns) * MAX_LAG_FRAC))
    if max_lag <= MIN_LAG:
        return None
    lags = np.arange(MIN_LAG, max_lag)

    rs_values = []
    for lag in lags:
        # Split into non-overlapping windows of size `lag`
        segments = len(log_returns) // lag
        if segments < 2:
            continue
        rs_list = []
        for i in range(segments):
            chunk = log_returns[i * lag: (i + 1) * lag]
            if len(chunk) < 2:
                continue
            mean = chunk.mean()
            deviations = chunk - mean
            cumulative = np.cumsum(deviations)
            R = cumulative.max() - cumulative.min()
            S = chunk.std(ddof=0)
            if S == 0:
                continue
            rs_list.append(R / S)
        if rs_list:
            rs_values.append((lag, np.mean(rs_list)))

    if len(rs_values) < 4:
        return None

    logs = np.log([r[0] for r in rs_values])
    log_rs = np.log([r[1] for r in rs_values])
    if not np.all(np.isfinite(log_rs)):
        return None
    # Linear fit: slope is H
    slope, _ = np.polyfit(logs, log_rs, 1)
    return float(slope)


def hurst_regime(h: float | None) -> str | None:
    """Classify a Hurst value into {'momentum','mean_revert','random'}."""
    if h is None:
        return None
    if h > 0.55:
        return "momentum"
    if h < 0.45:
        return "mean_revert"
    return "random"
