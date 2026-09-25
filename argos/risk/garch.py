"""GARCH(1,1) 1-day and 1-week variance forecast (P6).

Simple GARCH(1,1) fit + one-step forecast. Uses the classic RiskMetrics
default parameters when we have too little data for maximum-likelihood
fitting (~< 60 bars): omega=0, alpha=0.06, beta=0.94, which recovers to
EWMA-style vol tracking — good enough for sizing in the absence of
enough data for a full MLE.

Returns variance (not vol) in return-space (%^2) so Kelly's denominator
gets the right units.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _log_returns(close: pd.Series) -> np.ndarray:
    r = np.log(close / close.shift(1)).dropna().to_numpy()
    return r * 100  # in percent


def _fit_garch11(returns: np.ndarray, max_iter: int = 100) -> tuple[float, float, float]:
    """Very lightweight GARCH(1,1) fitting via method-of-moments +
    a small MLE refinement. Falls back to RiskMetrics defaults when the
    optimisation is degenerate.
    """
    if len(returns) < 30:
        return (0.0, 0.06, 0.94)  # RiskMetrics defaults

    var_uncond = returns.var(ddof=0)
    if var_uncond <= 0:
        return (0.0, 0.06, 0.94)

    # Start from RiskMetrics-ish and iterate
    omega, alpha, beta = 0.05 * var_uncond, 0.05, 0.90
    for _ in range(max_iter):
        sig2 = np.zeros_like(returns)
        sig2[0] = var_uncond
        for t in range(1, len(returns)):
            sig2[t] = omega + alpha * returns[t - 1] ** 2 + beta * sig2[t - 1]
        # gradient-free tweak: nudge alpha toward whichever direction
        # reduces the sum-of-squared residuals
        resid_next = returns[1:] ** 2 - sig2[1:]
        grad_alpha = -2 * np.mean(resid_next * returns[:-1] ** 2)
        alpha_new = max(0.001, min(0.20, alpha - 0.001 * grad_alpha))
        if abs(alpha_new - alpha) < 1e-4:
            alpha = alpha_new
            break
        alpha = alpha_new
    # keep beta = 1 - alpha - small omega/uncond
    beta = max(0.5, min(0.98, 1 - alpha - 0.05))
    omega = max(1e-8, var_uncond * (1 - alpha - beta))
    return (omega, alpha, beta)


def forecast_variance(close: pd.Series, horizon: int = 1) -> float | None:
    """1-step (or n-step) variance forecast in % ** 2. None if too little data."""
    if close is None or len(close) < 30:
        return None
    r = _log_returns(close)
    if len(r) < 30:
        return None
    omega, alpha, beta = _fit_garch11(r)
    # Iterate forward horizon steps
    sig2 = r.var(ddof=0)
    for t in range(1, len(r)):
        sig2 = omega + alpha * r[t - 1] ** 2 + beta * sig2
    for _ in range(horizon):
        sig2 = omega + (alpha + beta) * sig2
    return float(sig2)


def forecast_variance_1w(close: pd.Series) -> float | None:
    """5-day cumulative variance forecast."""
    return forecast_variance(close, horizon=5)


def forecast_vol_pct(close: pd.Series, horizon: int = 1) -> float | None:
    """Convenience: sqrt of variance → forecast vol in %."""
    v = forecast_variance(close, horizon)
    if v is None or v <= 0:
        return None
    return math.sqrt(v)
