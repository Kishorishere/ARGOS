"""Deflated Sharpe ratio (P16) — Bailey & López de Prado (2014).

The problem: you try 30 variants of a strategy; the best one has a great
Sharpe by chance. Deflating corrects for the number of trials — anything
lower than the "expected max Sharpe under null" is not real edge.

Implementation follows Bailey & López de Prado:

    DSR = Prob( SR > SR_hat )
        = Φ( ((SR - E[max_SR]) * sqrt(T - 1))
              / sqrt(1 - γ*SR + (γ-1)/4 * SR^2) )

where E[max_SR] under N trials ≈ σ_SR × ((1 - γ_EM) * Φ⁻¹(1 - 1/N)
                                          + γ_EM * Φ⁻¹(1 - 1/(N*e)))
and γ = skew, γ-1 = (kurt - 3) for the strategy's returns.

Returns a probability in (0, 1). DSR > 0.95 ≈ real signal.
"""

from __future__ import annotations

import math
from statistics import NormalDist


def sharpe_ratio(returns: list[float]) -> float:
    """Annualized Sharpe on a return series (daily returns, in % or fraction)."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    sd = var ** 0.5
    if sd == 0:
        return 0.0
    return mean / sd * math.sqrt(252)


def _moments(returns: list[float]) -> tuple[float, float, float, float]:
    """Return (mean, std, skew, kurtosis) of the series."""
    n = len(returns)
    if n < 2:
        return (0.0, 0.0, 0.0, 3.0)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    sd = var ** 0.5
    if sd == 0:
        return (mean, 0.0, 0.0, 3.0)
    skew = sum((r - mean) ** 3 for r in returns) / (n * sd ** 3)
    kurt = sum((r - mean) ** 4 for r in returns) / (n * sd ** 4)
    return (mean, sd, skew, kurt)


def deflated_sharpe(returns: list[float], n_trials: int = 1) -> dict:
    """Compute deflated Sharpe probability.

    Args:
        returns: strategy's PERIOD returns (usually daily), decimal or %
        n_trials: how many strategy variants you compared to pick this one

    Returns:
        {
          "sharpe":                raw annualized Sharpe,
          "expected_max_sharpe":   E[max SR] under null with n_trials,
          "deflated_probability":  probability the raw SR is genuine,
          "verdict":               "real" | "borderline" | "not_significant",
        }
    """
    n = len(returns)
    if n < 5:
        return {"sharpe": 0.0, "expected_max_sharpe": 0.0,
                "deflated_probability": 0.0, "verdict": "insufficient_data"}
    _mean, _sd, skew, kurt = _moments(returns)
    sr = sharpe_ratio(returns)
    excess_kurt = kurt - 3  # centered

    # Expected max Sharpe under null (0 mean) with n_trials
    # Approximation: E[max Z] ≈ sqrt(2 * ln(n_trials)) for Gaussian; refine
    # with Bailey-López de Prado's correction:
    #   E[max SR] ≈ ((1 - γ_EM) * Φ⁻¹(1 - 1/N) + γ_EM * Φ⁻¹(1 - 1/(N*e))) * σ_SR
    gamma_em = 0.5772156649015329  # Euler-Mascheroni
    normal = NormalDist(0, 1)
    if n_trials <= 1:
        expected_max = 0.0
    else:
        try:
            expected_max = (
                (1 - gamma_em) * normal.inv_cdf(1 - 1.0 / n_trials)
                + gamma_em * normal.inv_cdf(1 - 1.0 / (n_trials * math.e))
            )
        except Exception:
            expected_max = math.sqrt(2 * math.log(max(n_trials, 2)))

    # DSR probability
    denom_inner = 1 - skew * sr + ((excess_kurt) / 4) * (sr ** 2)
    if denom_inner <= 0:
        denom_inner = 1e-6
    denom = math.sqrt(denom_inner)
    numer = (sr - expected_max) * math.sqrt(n - 1)
    dsr_prob = normal.cdf(numer / denom)

    if dsr_prob > 0.95:
        verdict = "real"
    elif dsr_prob > 0.75:
        verdict = "borderline"
    else:
        verdict = "not_significant"

    return {
        "sharpe": round(sr, 3),
        "expected_max_sharpe": round(expected_max, 3),
        "n_trials": n_trials,
        "deflated_probability": round(dsr_prob, 3),
        "skew": round(skew, 3),
        "kurt": round(kurt, 3),
        "verdict": verdict,
    }
