"""Candlestick reversals ("bull-ringer" family) and price/oscillator divergences.

Hand-rolled, dependency-free (pandas + numpy). Every detector is pure and
takes a small OHLC DataFrame; higher-level code decides how much weight to
give each signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ── Candle helpers ──────────────────────────────────────────────────────────


def _body(open_: float, close: float) -> float:
    return abs(close - open_)


def _upper_wick(o: float, h: float, c: float) -> float:
    return h - max(o, c)


def _lower_wick(o: float, l: float, c: float) -> float:
    return min(o, c) - l


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def _is_bull(o: float, c: float) -> bool:
    return c > o


def _is_bear(o: float, c: float) -> bool:
    return c < o


# ── Single-bar patterns ─────────────────────────────────────────────────────


def is_doji(o: float, h: float, l: float, c: float, body_pct: float = 0.1) -> bool:
    """Body is < body_pct of total range."""
    rng = _range(h, l)
    return _body(o, c) / rng < body_pct


def is_hammer(o: float, h: float, l: float, c: float) -> bool:
    """Small body near top of range, long lower wick, minimal upper wick.

    Standard definition: body ≤ 30% of range, lower wick ≥ 2× body, and
    upper wick is a small fraction of the range (≤ 15%).
    """
    body = _body(o, c)
    rng = _range(h, l)
    if body / rng > 0.30:
        return False
    lower = _lower_wick(o, l, c)
    upper = _upper_wick(o, h, c)
    return lower >= 2 * body and upper / rng <= 0.15


def is_shooting_star(o: float, h: float, l: float, c: float) -> bool:
    """Small body near bottom of range, long upper wick, minimal lower wick."""
    body = _body(o, c)
    rng = _range(h, l)
    if body / rng > 0.30:
        return False
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    return upper >= 2 * body and lower / rng <= 0.15


# ── Two-bar patterns ────────────────────────────────────────────────────────


def is_bullish_engulfing(prev_o, prev_c, o, c) -> bool:
    """Prev bar bearish, current bar bullish and body engulfs prev body."""
    return (
        _is_bear(prev_o, prev_c)
        and _is_bull(o, c)
        and c >= prev_o
        and o <= prev_c
    )


def is_bearish_engulfing(prev_o, prev_c, o, c) -> bool:
    return (
        _is_bull(prev_o, prev_c)
        and _is_bear(o, c)
        and o >= prev_c
        and c <= prev_o
    )


def is_piercing_line(prev_o, prev_c, o, c) -> bool:
    """Bear bar then bull bar opening below prev low, closing above prev midpoint."""
    if not (_is_bear(prev_o, prev_c) and _is_bull(o, c)):
        return False
    prev_mid = (prev_o + prev_c) / 2
    return o < prev_c and c > prev_mid and c < prev_o


def is_dark_cloud_cover(prev_o, prev_c, o, c) -> bool:
    if not (_is_bull(prev_o, prev_c) and _is_bear(o, c)):
        return False
    prev_mid = (prev_o + prev_c) / 2
    return o > prev_c and c < prev_mid and c > prev_o


def is_tweezer_bottom(prev_l, l, tol_pct: float = 0.002) -> bool:
    if prev_l == 0:
        return False
    return abs(prev_l - l) / max(prev_l, 1e-9) < tol_pct


def is_tweezer_top(prev_h, h, tol_pct: float = 0.002) -> bool:
    if prev_h == 0:
        return False
    return abs(prev_h - h) / max(prev_h, 1e-9) < tol_pct


def is_inside_bar(prev_h, prev_l, h, l) -> bool:
    return h <= prev_h and l >= prev_l


def is_outside_bar(prev_h, prev_l, h, l) -> bool:
    return h >= prev_h and l <= prev_l


# ── Three-bar patterns ──────────────────────────────────────────────────────


def is_morning_star(o1, c1, o2, c2, o3, c3) -> bool:
    """Bear bar, small-body star, bull bar closing into first body."""
    if not _is_bear(o1, c1):
        return False
    small_body = _body(o2, c2) < 0.5 * _body(o1, c1)
    if not small_body:
        return False
    if not _is_bull(o3, c3):
        return False
    return c3 > (o1 + c1) / 2


def is_evening_star(o1, c1, o2, c2, o3, c3) -> bool:
    if not _is_bull(o1, c1):
        return False
    small_body = _body(o2, c2) < 0.5 * _body(o1, c1)
    if not small_body:
        return False
    if not _is_bear(o3, c3):
        return False
    return c3 < (o1 + c1) / 2


def is_three_white_soldiers(opens: list[float], closes: list[float]) -> bool:
    if len(opens) < 3 or len(closes) < 3:
        return False
    a, b, c = -3, -2, -1
    all_bull = all(_is_bull(opens[i], closes[i]) for i in (a, b, c))
    higher_closes = closes[c] > closes[b] > closes[a]
    opens_inside_prev = (
        opens[b] > opens[a] and opens[b] < closes[a] and
        opens[c] > opens[b] and opens[c] < closes[b]
    )
    return all_bull and higher_closes and opens_inside_prev


def is_three_black_crows(opens: list[float], closes: list[float]) -> bool:
    if len(opens) < 3 or len(closes) < 3:
        return False
    a, b, c = -3, -2, -1
    all_bear = all(_is_bear(opens[i], closes[i]) for i in (a, b, c))
    lower_closes = closes[c] < closes[b] < closes[a]
    opens_inside_prev = (
        opens[b] < opens[a] and opens[b] > closes[a] and
        opens[c] < opens[b] and opens[c] > closes[b]
    )
    return all_bear and lower_closes and opens_inside_prev


# ── Divergence detection ────────────────────────────────────────────────────


def _find_pivots(series: pd.Series, left: int = 2, right: int = 2) -> list[int]:
    """Return indices of local extrema. A pivot low needs `left` bars strictly
    higher on the left and `right` strictly higher on the right (mirror for
    highs). Small window keeps this cheap.
    """
    lows: list[int] = []
    highs: list[int] = []
    values = series.to_numpy()
    n = len(values)
    for i in range(left, n - right):
        window_l = values[i - left : i]
        window_r = values[i + 1 : i + 1 + right]
        if np.all(values[i] < window_l) and np.all(values[i] < window_r):
            lows.append(i)
        if np.all(values[i] > window_l) and np.all(values[i] > window_r):
            highs.append(i)
    return lows, highs


def detect_divergences(
    close: pd.Series,
    oscillator: pd.Series,
    lookback: int = 60,
    pivot_left: int = 2,
    pivot_right: int = 2,
) -> dict:
    """Regular + hidden bull/bear divergence between price and any oscillator.

    Regular bullish: price makes lower low, oscillator makes higher low.
    Regular bearish: price makes higher high, oscillator makes lower high.
    Hidden bullish (continuation): price higher low, oscillator lower low.
    Hidden bearish (continuation): price lower high, oscillator higher high.
    """
    result = {
        "regular_bullish": False,
        "regular_bearish": False,
        "hidden_bullish": False,
        "hidden_bearish": False,
        "strength": 0.0,
    }
    if len(close) < lookback or len(oscillator) < lookback:
        return result
    tail_close = close.tail(lookback).reset_index(drop=True)
    tail_osc = oscillator.tail(lookback).reset_index(drop=True).dropna()
    if len(tail_osc) < lookback // 2:
        return result

    price_lows, price_highs = _find_pivots(tail_close, pivot_left, pivot_right)
    osc_lows, osc_highs = _find_pivots(tail_osc, pivot_left, pivot_right)

    # Regular bullish: last two price lows, last two osc lows near them
    if len(price_lows) >= 2 and len(osc_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        o1, o2 = osc_lows[-2], osc_lows[-1]
        if tail_close.iloc[p2] < tail_close.iloc[p1] and tail_osc.iloc[o2] > tail_osc.iloc[o1]:
            result["regular_bullish"] = True
        if tail_close.iloc[p2] > tail_close.iloc[p1] and tail_osc.iloc[o2] < tail_osc.iloc[o1]:
            result["hidden_bullish"] = True

    # Regular bearish: last two price highs, last two osc highs
    if len(price_highs) >= 2 and len(osc_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        o1, o2 = osc_highs[-2], osc_highs[-1]
        if tail_close.iloc[p2] > tail_close.iloc[p1] and tail_osc.iloc[o2] < tail_osc.iloc[o1]:
            result["regular_bearish"] = True
        if tail_close.iloc[p2] < tail_close.iloc[p1] and tail_osc.iloc[o2] > tail_osc.iloc[o1]:
            result["hidden_bearish"] = True

    result["strength"] = float(
        result["regular_bullish"] + result["regular_bearish"]
        + 0.5 * (result["hidden_bullish"] + result["hidden_bearish"])
    )
    return result
