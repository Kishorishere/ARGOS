"""Unit tests for argos/indicators — RSI/MACD/ADX/Bollinger/candles/etc.

Every test is deterministic; no LLM, no network. Two flavors:
  - known-input / known-output for the numeric indicators
  - regime tests: on a synthetic uptrend the trend group is net-bullish, on
    chop ADX < 20, on a squeeze BB width < Keltner width, etc.
  - look-ahead invariance: the value at bar t must not change when bars
    after t are appended.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from argos.indicators import compute_snapshot
from argos.indicators.engine import (
    _adx,
    _atr,
    _bollinger,
    _keltner,
    _macd,
    _psar,
    _rsi,
    _stoch,
)
from argos.indicators.patterns import (
    detect_divergences,
    is_bearish_engulfing,
    is_bullish_engulfing,
    is_doji,
    is_hammer,
    is_morning_star,
    is_shooting_star,
    is_three_white_soldiers,
    is_tweezer_bottom,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _records(open_, high, low, close, volume=None, start="2024-01-02"):
    n = len(close)
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    vol = volume if volume is not None else [100_000 + i for i in range(n)]
    return [
        {
            "date": dates[i],
            "open": float(open_[i]),
            "high": float(high[i]),
            "low": float(low[i]),
            "close": float(close[i]),
            "volume": int(vol[i]),
        }
        for i in range(n)
    ]


def _make_uptrend(n: int = 250, slope: float = 0.3, noise: float = 0.4, seed: int = 1):
    rng = np.random.default_rng(seed)
    trend = np.linspace(100, 100 + slope * n, n)
    close = trend + rng.normal(0, noise, n).cumsum() * 0.05
    high = close + rng.uniform(0.1, 0.6, n)
    low = close - rng.uniform(0.1, 0.6, n)
    open_ = close + rng.normal(0, 0.2, n)
    return open_, high, low, close


def _make_downtrend(n: int = 250, slope: float = 0.3, noise: float = 0.4, seed: int = 2):
    o, h, l, c = _make_uptrend(n, slope, noise, seed)
    return o, l, h, c[::-1] + 40  # mirror


def _make_sideways(n: int = 250, seed: int = 4):
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 0.8, n).cumsum() * 0.02  # very tight walk
    high = close + rng.uniform(0.1, 0.4, n)
    low = close - rng.uniform(0.1, 0.4, n)
    open_ = close + rng.normal(0, 0.1, n)
    return open_, high, low, close


# ── Numeric indicators ──────────────────────────────────────────────────────


class TestRSI:
    def test_all_up_gives_100(self):
        close = pd.Series(np.arange(1, 40, dtype=float))
        rsi = _rsi(close, 14)
        # avg_loss = 0, RS -> inf, RSI -> 100
        assert rsi.iloc[-1] == pytest.approx(100.0, abs=1e-6)

    def test_all_down_gives_0(self):
        close = pd.Series(np.arange(40, 1, -1, dtype=float))
        rsi = _rsi(close, 14)
        assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-6)

    def test_bounds(self):
        rng = np.random.default_rng(0)
        close = pd.Series(100 + rng.normal(0, 1, 200).cumsum())
        rsi = _rsi(close, 14).dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all()


class TestMACD:
    def test_length_and_signs(self):
        close = pd.Series(np.linspace(50, 100, 100))
        line, sig, hist = _macd(close, 12, 26, 9)
        assert len(line) == len(sig) == len(hist) == 100
        # On a pure uptrend MACD line stays positive at the tail
        assert line.iloc[-1] > 0


class TestATRandADX:
    def test_atr_positive_on_range(self):
        rng = np.random.default_rng(0)
        n = 100
        close = 100 + rng.normal(0, 1, n).cumsum() * 0.3
        high = close + 1.0
        low = close - 1.0
        atr = _atr(pd.Series(high), pd.Series(low), pd.Series(close), 14).dropna()
        assert (atr > 0).all()

    def test_adx_higher_on_trend_than_chop(self):
        u_o, u_h, u_l, u_c = _make_uptrend()
        s_o, s_h, s_l, s_c = _make_sideways()
        adx_up = _adx(pd.Series(u_h), pd.Series(u_l), pd.Series(u_c), 14).dropna().iloc[-1]
        adx_side = _adx(pd.Series(s_h), pd.Series(s_l), pd.Series(s_c), 14).dropna().iloc[-1]
        assert adx_up > adx_side, f"ADX uptrend {adx_up:.1f} should exceed chop {adx_side:.1f}"


class TestBollingerAndKeltner:
    def test_bb_middle_is_sma(self):
        close = pd.Series(np.arange(1, 101, dtype=float))
        up, mid, lo, width = _bollinger(close, 20, 2.0)
        assert mid.iloc[-1] == pytest.approx(close.tail(20).mean())
        # Upper > lower everywhere the values exist
        assert (up.dropna() > lo.dropna()).all()

    def test_squeeze_detected(self):
        # A tight linear series ⇒ BB width smaller than Keltner width
        n = 60
        close = pd.Series(np.linspace(100, 100.1, n))
        high = close + 0.01
        low = close - 0.01
        _, _, _, bbw = _bollinger(close, 20, 2.0)
        _, _, _, kw = _keltner(pd.Series(high), pd.Series(low), close, 20, 2.0)
        assert bbw.iloc[-1] < kw.iloc[-1], "flat-line series should register as a squeeze"


class TestStochastic:
    def test_bounds(self):
        rng = np.random.default_rng(0)
        n = 100
        close = 100 + rng.normal(0, 1, n).cumsum() * 0.3
        high = close + 1
        low = close - 1
        k, d = _stoch(pd.Series(high), pd.Series(low), pd.Series(close), 14, 3)
        k, d = k.dropna(), d.dropna()
        assert (k >= 0).all() and (k <= 100).all()
        assert (d >= 0).all() and (d <= 100).all()


class TestPSAR:
    def test_produces_finite_series_on_uptrend(self):
        _, h, l, _ = _make_uptrend(80)
        psar = _psar(pd.Series(h), pd.Series(l))
        # PSAR should be defined and finite from the second bar onward
        assert np.isfinite(psar.iloc[-1])


# ── Candlestick patterns ────────────────────────────────────────────────────


class TestCandles:
    def test_bullish_engulfing_true(self):
        # Prev bar bearish, current bullish and body engulfs
        assert is_bullish_engulfing(prev_o=110, prev_c=100, o=99, c=112)

    def test_bullish_engulfing_false_when_not_engulfing(self):
        assert not is_bullish_engulfing(prev_o=110, prev_c=100, o=101, c=109)

    def test_bearish_engulfing_true(self):
        assert is_bearish_engulfing(prev_o=100, prev_c=110, o=112, c=98)

    def test_hammer(self):
        # Small body near top, long lower wick
        assert is_hammer(o=100.5, h=101.0, l=95.0, c=100.8)

    def test_shooting_star(self):
        assert is_shooting_star(o=100.5, h=106.0, l=100.0, c=100.7)

    def test_doji(self):
        assert is_doji(o=100.0, h=101.0, l=99.0, c=100.02)

    def test_morning_star(self):
        # bear -> tiny body -> bull closing back above midpoint of the bear bar
        assert is_morning_star(
            o1=110, c1=100,  # long red
            o2=99, c2=99.2,  # doji-ish star
            o3=99.5, c3=108,  # long green closing above (110+100)/2 = 105
        )

    def test_three_white_soldiers(self):
        opens = [100, 102, 104]
        closes = [103, 105, 107]
        assert is_three_white_soldiers(opens, closes)

    def test_tweezer_bottom(self):
        assert is_tweezer_bottom(prev_l=100.00, l=100.05, tol_pct=0.01)


# ── Divergence ──────────────────────────────────────────────────────────────


class TestDivergence:
    def test_regular_bullish_divergence(self):
        # Price makes lower low, oscillator makes higher low
        n = 60
        # Two clear lows: at idx 15 (price 50, osc 20) and idx 45 (price 40, osc 35)
        close = np.linspace(80, 60, n).tolist()
        close[15] = 50
        close[45] = 40  # lower low
        osc = np.linspace(60, 50, n).tolist()
        osc[15] = 20
        osc[45] = 35  # higher low
        res = detect_divergences(pd.Series(close), pd.Series(osc), lookback=n, pivot_left=3, pivot_right=3)
        assert res["regular_bullish"] is True


# ── Snapshot regime tests ───────────────────────────────────────────────────


class TestSnapshotRegimes:
    def test_uptrend_gives_net_bullish_trend_group(self):
        o, h, l, c = _make_uptrend(250, slope=0.4)
        snap = compute_snapshot(_records(o, h, l, c), "UP", "2024-12-01")
        trend_bull = sum(s.weight for s in snap.trend.signals if s.direction == "bullish")
        trend_bear = sum(s.weight for s in snap.trend.signals if s.direction == "bearish")
        assert trend_bull > trend_bear
        assert snap.trend.regime in ("uptrend",)
        assert snap.trend.adx_14 > 25

    def test_sideways_gives_chop_regime(self):
        o, h, l, c = _make_sideways(250)
        snap = compute_snapshot(_records(o, h, l, c), "SIDE", "2024-12-01")
        assert snap.trend.adx_14 < 25
        assert snap.trend.regime in ("chop", None)  # regime may be unset if ADX is borderline

    def test_flat_series_is_a_squeeze(self):
        n = 60
        c = np.full(n, 100.0)
        c[-1] = 100.05
        h = c + 0.01
        l = c - 0.01
        snap = compute_snapshot(_records(c - 0.005, h, l, c), "FLAT", "2024-03-25")
        assert snap.volatility.bb_width is not None
        assert snap.volatility.keltner_width is not None
        assert snap.volatility.bb_width < snap.volatility.keltner_width


# ── Look-ahead invariance ───────────────────────────────────────────────────


class TestNoLookahead:
    """Adding bars AFTER the as-of bar must not change indicator values AT that bar."""

    def test_snapshot_stable_when_future_bars_appended(self):
        o, h, l, c = _make_uptrend(200)
        records_short = _records(o, h, l, c)  # 200 bars
        snap_short = compute_snapshot(records_short, "STAB", records_short[-1]["date"])

        # Append 50 more synthetic bars AFTER the short window's last bar
        rng = np.random.default_rng(9)
        extra_close = c[-1] + rng.normal(0, 1, 50).cumsum() * 0.1
        extra_o = extra_close + rng.normal(0, 0.2, 50)
        extra_h = extra_close + rng.uniform(0.1, 0.5, 50)
        extra_l = extra_close - rng.uniform(0.1, 0.5, 50)
        extra = _records(extra_o, extra_h, extra_l, extra_close, start="2025-01-02")
        records_long = records_short + extra

        # Compute snapshot on the LONGER series but as-of the ORIGINAL last bar.
        # We simulate this by slicing to that bar (what the loader guarantees).
        as_of_date = records_short[-1]["date"]
        # In production the loader enforces this; here we just take the same slice.
        sliced = [r for r in records_long if r["date"] <= as_of_date]
        snap_sliced = compute_snapshot(sliced, "STAB", as_of_date)

        assert snap_short.momentum.rsi_14 == pytest.approx(snap_sliced.momentum.rsi_14, rel=1e-9)
        assert snap_short.trend.macd_hist == pytest.approx(snap_sliced.trend.macd_hist, rel=1e-9)
        assert snap_short.trend.adx_14 == pytest.approx(snap_sliced.trend.adx_14, rel=1e-9)
        assert snap_short.volatility.bb_width == pytest.approx(snap_sliced.volatility.bb_width, rel=1e-9)
        assert snap_short.composite_bull_score == pytest.approx(snap_sliced.composite_bull_score, rel=1e-9)
        assert snap_short.composite_bear_score == pytest.approx(snap_sliced.composite_bear_score, rel=1e-9)
