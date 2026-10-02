"""Regime-conditioned indicator weights (U6).

The baseline `engine.py` fires signals with static weights regardless of
market regime. That's why RSI-oversold at 27 in a raging downtrend fires
with the same weight as RSI-27 in a mean-reverting chop — and gets us
into knife-catch losers.

This module returns a multiplier for each indicator name based on the
current `trend.regime` and `volatility.vol_regime`. Applied AFTER the raw
signals fire, before the composite_bull/bear_score is summed.

Design principles:
- **Uptrend regime**: trend-following gets 1.5-2x; mean-reversion gets 0.3x
  (don't fade a healthy trend).
- **Downtrend regime**: like uptrend but mean-reversion is faded HARDER
  (0.15x) — the "RSI 30 = buy" reflex is what turns bear markets into
  drawdowns; and Bollinger-breakout-up gets faded (fakeouts) while
  breakdown gets boosted (aligns with regime). Merged from terminal's
  `_REGIME_WEIGHTS` bear column.
- **Chop regime**: mean-reversion indicators (RSI oversold, Stoch <20,
  BB-touch, Williams %R) get 1.5-2x; trend-following gets 0.4-0.6x.
- **High-vol regime**: cut all weights by ~40% — signals are less reliable
  when the market is thrashing. Uncertainty tax.
"""

from __future__ import annotations

from typing import Literal


TrendRegime = Literal["uptrend", "downtrend", "chop", None]
VolRegime = Literal["low", "mid", "high", None]


# Per-regime weight multipliers keyed by indicator "family" (matching the
# `indicator` prefix set in engine.py::ThresholdSignal.indicator strings).
# Any indicator not listed here gets 1.0 (no adjustment).
_UPTREND_MULT = {
    # Trend-following: full weight in trend regimes
    "MACD(12,26,9)":         1.5,
    "MACD hist flip":        1.5,
    "EMA9 vs EMA21":         1.3,
    "Golden/Death cross":    1.5,
    "ADX(14)":               1.5,
    "20d high break":        1.5,
    "20d low break":         1.5,
    # Mean-reversion: FADED in trend regimes (don't catch knives)
    "RSI(14)":               0.3,
    "Stoch %K/%D":           0.3,
    "Williams %R(14)":       0.3,
    "Close z(20)":           0.4,
    "Bollinger breakout up": 1.2,   # breakout IS trend continuation
    "Bollinger breakdown":   1.2,
    "MFI(14)":               0.6,
    # Volume + candle patterns: neutral
}

# Downtrend variant — merged from terminal's bear-regime column. The
# insight isn't that mean-reversion is DIFFERENTLY biased in bear vs bull
# (Argos indicators are direction-symmetric), but that the RSI-30-buy
# reflex is a much bigger drawdown magnet in a persistent downtrend, and
# breakouts up in a downtrend fake out more often.
_DOWNTREND_MULT = {
    # Trend-following: still full weight (a fresh trend signal in a bear
    # regime is by definition either continuation or a real turning point)
    "MACD(12,26,9)":         1.5,
    "MACD hist flip":        1.5,
    "EMA9 vs EMA21":         1.3,
    "Golden/Death cross":    1.5,
    "ADX(14)":               1.5,
    "20d high break":        1.5,
    "20d low break":         1.5,
    # Mean-reversion: FADED HARDER than in uptrend (terminal bear=0.3 col)
    "RSI(14)":               0.15,
    "Stoch %K/%D":           0.15,
    "Williams %R(14)":       0.15,
    "Close z(20)":           0.25,
    "MFI(14)":               0.4,
    # Bollinger asymmetry — breakouts UP in a downtrend are fakeouts;
    # breakdowns confirm the regime.
    "Bollinger breakout up": 0.7,
    "Bollinger breakdown":   1.5,
    # Candle reversals: usable but keep humble in bear (many failed bounces)
    "Candle: bullish_engulfing": 0.7,
    "Candle: hammer":            0.7,
    "Candle: morning_star":      0.9,
    "Candle: bearish_engulfing": 1.3,
    "Candle: shooting_star":     1.3,
    "Candle: evening_star":      1.3,
}

_CHOP_MULT = {
    # Mean-reversion: full weight in chop
    "RSI(14)":               1.7,
    "Stoch %K/%D":           1.7,
    "Williams %R(14)":       1.5,
    "Close z(20)":           1.5,
    "MFI(14)":               1.5,
    "Bollinger breakout up": 0.7,   # breakouts often fail in chop
    "Bollinger breakdown":   0.7,
    # Trend-following: FADED in chop (whipsawing)
    "MACD(12,26,9)":         0.5,
    "MACD hist flip":        0.6,
    "EMA9 vs EMA21":         0.5,
    "Golden/Death cross":    0.8,   # long-horizon; not as noisy
    "ADX(14)":               0.6,   # by definition low in chop
    "20d high break":        0.7,
    "20d low break":         0.7,
    # Candles + divergences: strong in chop
    "Candle: bullish_engulfing":     1.4,
    "Candle: bearish_engulfing":     1.4,
    "Candle: hammer":                1.3,
    "Candle: shooting_star":         1.3,
    "Candle: morning_star":          1.5,
    "Candle: evening_star":          1.5,
    "Candle: three_white_soldiers":  1.3,
    "Candle: three_black_crows":     1.3,
}


def _multiplier_for(indicator: str, trend_regime: TrendRegime,
                    vol_regime: VolRegime) -> float:
    """Look up the multiplier by indicator name + regime.

    Falls back to 1.0 for anything unknown so we don't accidentally zero
    out new indicators that get added later.
    """
    if trend_regime == "uptrend":
        base = _UPTREND_MULT.get(indicator, 1.0)
    elif trend_regime == "downtrend":
        base = _DOWNTREND_MULT.get(indicator, 1.0)
    elif trend_regime == "chop":
        base = _CHOP_MULT.get(indicator, 1.0)
    else:
        base = 1.0  # regime unknown → don't touch it
    # Vol regime tax: high-vol shrinks EVERYTHING because signals are noisier
    if vol_regime == "high":
        base *= 0.6
    elif vol_regime == "low":
        base *= 1.1  # tiny boost when signals are cleaner
    return base


def apply_regime_weights(fired_signals: list, trend_regime: TrendRegime,
                         vol_regime: VolRegime) -> list:
    """Return a new list of ThresholdSignal-like objects with `weight`
    scaled by the regime multiplier. Original signals are not mutated.
    """
    out = []
    for sig in fired_signals:
        m = _multiplier_for(sig.indicator, trend_regime, vol_regime)
        adjusted = sig.model_copy(update={
            "weight": sig.weight * m,
            "note": f"{sig.note} [regime×{m:.2f}]" if m != 1.0 else sig.note,
        })
        out.append(adjusted)
    return out
