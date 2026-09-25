"""Vol-targeted position sizing (P18).

Pro trick: instead of sizing by conviction alone, target a PORTFOLIO
volatility budget (e.g., 15% annualized). Each position's weight is
inversely proportional to its own daily vol so that a single
lower-vol position and a single higher-vol position contribute EQUALLY
to portfolio variance.

Formula (single-asset case):
    daily_target_vol = annual_target / sqrt(252)
    weight = daily_target_vol / asset_daily_vol × conviction

Used in `portfolio_manager` alongside Kelly-fractional: take min(kelly, vol_target).
"""

from __future__ import annotations

import math


ANNUAL_VOL_TARGET_DEFAULT = 15.0  # percent


def vol_target_weight(asset_daily_vol_pct: float,
                      annual_vol_target_pct: float = ANNUAL_VOL_TARGET_DEFAULT,
                      conviction: float = 1.0,
                      max_weight: float = 0.25) -> float:
    """Return the position weight that targets `annual_vol_target_pct` at
    portfolio level, scaled by conviction (0..1).

    Args:
        asset_daily_vol_pct: forecasted daily vol of the underlying, in %
        annual_vol_target_pct: portfolio-level annualized vol target
        conviction: 0..1 multiplier from signal quality
        max_weight: hard cap
    """
    if asset_daily_vol_pct is None or asset_daily_vol_pct <= 0:
        return 0.0
    daily_target = annual_vol_target_pct / math.sqrt(252)
    raw = (daily_target / asset_daily_vol_pct) * max(0.0, min(1.0, conviction))
    return max(0.0, min(raw, max_weight))
