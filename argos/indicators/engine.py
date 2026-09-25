"""compute_snapshot(ohlcv_records, ticker, as_of_date) -> IndicatorSnapshot.

Hand-rolled indicators — no ta-lib / pandas-ta dependency. Every indicator
appends `ThresholdSignal`s to its group so the LLM sees a labelled reason,
not a bare number, and the journal can trace the composite bias back to
individual firing rules.

Weighting: any single-indicator weight is small (1.0–2.0). Composite
bull/bear scores are unbounded sums; downstream code should use the ratio
(net / total_fired) rather than the raw sum.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from argos.indicators import patterns
from argos.indicators.freshness import apply_freshness
from argos.indicators.hurst import hurst_exponent, hurst_regime
from argos.indicators.schemas import (
    Candles,
    Divergences,
    IndicatorSnapshot,
    Momentum,
    Structure,
    ThresholdSignal,
    Trend,
    Volatility,
    Volume,
)
from argos.indicators.weights import apply_regime_weights
from argos.learn import get_weight_multiplier, load_ledger

# Cache the ledger at module load; new backtests should pick it up
# by calling reload_ledger() if needed.
_LEDGER = None


def reload_ledger():
    global _LEDGER
    _LEDGER = load_ledger()
    return _LEDGER


def _ledger():
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = load_ledger()
    return _LEDGER

# ── Frame helpers ───────────────────────────────────────────────────────────


def _to_frame(ohlcv_records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv_records)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def _last(series: pd.Series) -> float | None:
    if series is None or len(series) == 0:
        return None
    val = series.iloc[-1]
    if pd.isna(val):
        return None
    return float(val)


# ── Core indicators ─────────────────────────────────────────────────────────


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder's smoothing == EMA with alpha = 1/period, adjust=False
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    # Convention: avg_loss==0 with avg_gain>0 => RSI=100; avg_gain==0 with
    # avg_loss>0 => RSI=0; both zero (flat) => NaN (undefined).
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    up_only = (avg_loss == 0) & (avg_gain > 0)
    down_only = (avg_gain == 0) & (avg_loss > 0)
    rsi = rsi.mask(up_only, 100.0)
    rsi = rsi.mask(down_only, 0.0)
    rsi = rsi.mask(both_zero, np.nan)
    return rsi


def _stoch(high: pd.Series, low: pd.Series, close: pd.Series,
           k: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    ll = low.rolling(k).min()
    hh = high.rolling(k).max()
    percent_k = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    percent_d = percent_k.rolling(d).mean()
    return percent_k, percent_d


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
          ) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=signal, adjust=False).mean()
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up = high.diff()
    dn = -low.diff()
    plus_dm = ((up > dn) & (up > 0)).astype(float) * up.clip(lower=0)
    minus_dm = ((dn > up) & (dn > 0)).astype(float) * dn.clip(lower=0)
    tr = _true_range(high, low, close)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _aroon(high: pd.Series, low: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series]:
    up = high.rolling(period + 1).apply(lambda x: 100 * (period - (period - np.argmax(x))) / period, raw=True)
    dn = low.rolling(period + 1).apply(lambda x: 100 * (period - (period - np.argmin(x))) / period, raw=True)
    return up, dn


def _psar(high: pd.Series, low: pd.Series, af_start: float = 0.02,
          af_step: float = 0.02, af_max: float = 0.2) -> pd.Series:
    n = len(high)
    if n < 3:
        return pd.Series([np.nan] * n, index=high.index)
    psar = np.zeros(n)
    trend_up = True
    af = af_start
    ep = high.iloc[0]
    psar[0] = low.iloc[0]
    for i in range(1, n):
        prev = psar[i - 1]
        if trend_up:
            psar[i] = prev + af * (ep - prev)
            psar[i] = min(psar[i], low.iloc[i - 1], low.iloc[i])
            if high.iloc[i] > ep:
                ep = high.iloc[i]
                af = min(af + af_step, af_max)
            if low.iloc[i] < psar[i]:
                trend_up = False
                psar[i] = ep
                ep = low.iloc[i]
                af = af_start
        else:
            psar[i] = prev + af * (ep - prev)
            psar[i] = max(psar[i], high.iloc[i - 1], high.iloc[i])
            if low.iloc[i] < ep:
                ep = low.iloc[i]
                af = min(af + af_step, af_max)
            if high.iloc[i] > psar[i]:
                trend_up = True
                psar[i] = ep
                ep = high.iloc[i]
                af = af_start
    return pd.Series(psar, index=high.index)


def _bollinger(close: pd.Series, period: int = 20, mult: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + mult * std
    lower = mid - mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return upper, mid, lower, width


def _keltner(high: pd.Series, low: pd.Series, close: pd.Series,
             period: int = 20, mult: float = 2.0):
    typical = (high + low + close) / 3
    mid = typical.ewm(span=period, adjust=False).mean()
    atr = _atr(high, low, close, period=period)
    upper = mid + mult * atr
    lower = mid - mult * atr
    width = (upper - lower) / mid.replace(0, np.nan)
    return upper, mid, lower, width


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    typical = (high + low + close) / 3
    return (typical * volume).cumsum() / volume.cumsum().replace(0, np.nan)


def _cmf(high, low, close, volume, period: int = 20) -> pd.Series:
    hl = (high - low).replace(0, np.nan)
    mfv = ((close - low) - (high - close)) / hl * volume
    return mfv.rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)


def _mfi(high, low, close, volume, period: int = 14) -> pd.Series:
    tp = (high + low + close) / 3
    mf = tp * volume
    delta = tp.diff()
    pos = mf.where(delta > 0, 0.0)
    neg = mf.where(delta < 0, 0.0)
    pos_sum = pos.rolling(period).sum()
    neg_sum = neg.rolling(period).sum().replace(0, np.nan)
    return 100 - (100 / (1 + pos_sum / neg_sum))


def _stoch_rsi(close, period: int = 14) -> pd.Series:
    rsi = _rsi(close, period)
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    return (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan) * 100


def _williams_r(high, low, close, period: int = 14) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    return -100 * (hh - close) / (hh - ll).replace(0, np.nan)


# ── Group builders ──────────────────────────────────────────────────────────


def _build_momentum(df: pd.DataFrame) -> Momentum:
    high, low, close = df["high"], df["low"], df["close"]
    m = Momentum()
    m.rsi_14 = _last(_rsi(close, 14))
    k, d = _stoch(high, low, close, 14, 3)
    m.stoch_k, m.stoch_d = _last(k), _last(d)
    m.stoch_rsi = _last(_stoch_rsi(close, 14))
    m.roc_10 = _last(close.pct_change(10) * 100)
    m.williams_r_14 = _last(_williams_r(high, low, close, 14))

    if m.rsi_14 is not None:
        if m.rsi_14 < 30:
            m.signals.append(ThresholdSignal(
                indicator="RSI(14)", value=m.rsi_14, threshold="<30",
                direction="bullish", weight=1.5,
                note="oversold — mean-reversion long bias",
            ))
        elif m.rsi_14 > 70:
            m.signals.append(ThresholdSignal(
                indicator="RSI(14)", value=m.rsi_14, threshold=">70",
                direction="bearish", weight=1.5,
                note="overbought — mean-reversion short bias",
            ))
    if m.stoch_k is not None and m.stoch_d is not None:
        if m.stoch_k < 20 and m.stoch_d < 20:
            m.signals.append(ThresholdSignal(
                indicator="Stoch %K/%D", value=m.stoch_k, threshold="<20",
                direction="bullish", weight=1.0, note="oversold",
            ))
        elif m.stoch_k > 80 and m.stoch_d > 80:
            m.signals.append(ThresholdSignal(
                indicator="Stoch %K/%D", value=m.stoch_k, threshold=">80",
                direction="bearish", weight=1.0, note="overbought",
            ))
    if m.williams_r_14 is not None:
        if m.williams_r_14 < -80:
            m.signals.append(ThresholdSignal(
                indicator="Williams %R(14)", value=m.williams_r_14, threshold="<-80",
                direction="bullish", weight=0.75, note="oversold",
            ))
        elif m.williams_r_14 > -20:
            m.signals.append(ThresholdSignal(
                indicator="Williams %R(14)", value=m.williams_r_14, threshold=">-20",
                direction="bearish", weight=0.75, note="overbought",
            ))
    return m


def _build_trend(df: pd.DataFrame) -> Trend:
    close, high, low = df["close"], df["high"], df["low"]
    t = Trend()
    t.ema_9 = _last(close.ewm(span=9, adjust=False).mean())
    t.ema_21 = _last(close.ewm(span=21, adjust=False).mean())
    t.ema_50 = _last(close.ewm(span=50, adjust=False).mean())
    t.ema_200 = _last(close.ewm(span=200, adjust=False).mean())
    t.sma_50 = _last(close.rolling(50).mean())
    t.sma_200 = _last(close.rolling(200).mean())

    macd_line, macd_signal, macd_hist = _macd(close)
    t.macd_line = _last(macd_line)
    t.macd_signal = _last(macd_signal)
    t.macd_hist = _last(macd_hist)
    if len(macd_hist) >= 2 and not pd.isna(macd_hist.iloc[-2]):
        t.macd_hist_prev = float(macd_hist.iloc[-2])

    t.adx_14 = _last(_adx(high, low, close, 14))
    up, dn = _aroon(high, low, 14)
    t.aroon_up, t.aroon_down = _last(up), _last(dn)
    t.psar = _last(_psar(high, low))

    last_close = _last(close)
    if last_close is not None and t.ema_200 is not None and t.ema_200 != 0:
        t.price_vs_ema200_pct = (last_close / t.ema_200 - 1) * 100

    # P13: Hurst regime
    h = hurst_exponent(close, lookback=60)
    t.hurst_exponent = round(h, 3) if h is not None else None
    t.hurst_regime = hurst_regime(h)

    # Regime classification — combine ADX + Hurst for cleaner detection
    if t.adx_14 is not None:
        if t.adx_14 >= 25 and t.price_vs_ema200_pct is not None:
            t.regime = "uptrend" if t.price_vs_ema200_pct > 0 else "downtrend"
        elif t.adx_14 < 20:
            t.regime = "chop"
    # Hurst overrides in edge cases: ADX borderline but Hurst clearly says
    # mean_revert → force "chop" so mean-reversion signals get boosted.
    if t.hurst_regime == "mean_revert" and t.regime is None:
        t.regime = "chop"

    # Signals
    if t.macd_line is not None and t.macd_signal is not None:
        if t.macd_line > t.macd_signal and t.macd_hist is not None and t.macd_hist > 0:
            direction = "bullish"
            note = "MACD above signal"
        elif t.macd_line < t.macd_signal and t.macd_hist is not None and t.macd_hist < 0:
            direction = "bearish"
            note = "MACD below signal"
        else:
            direction = "neutral"
            note = "MACD hovering"
        if direction != "neutral":
            t.signals.append(ThresholdSignal(
                indicator="MACD(12,26,9)", value=t.macd_hist,
                threshold="hist sign", direction=direction, weight=1.0, note=note,
            ))
    # MACD histogram flip (momentum shift)
    if t.macd_hist is not None and t.macd_hist_prev is not None:
        if t.macd_hist_prev < 0 <= t.macd_hist:
            t.signals.append(ThresholdSignal(
                indicator="MACD hist flip", value=t.macd_hist, threshold="crossed >=0",
                direction="bullish", weight=1.25, note="momentum turning positive",
            ))
        elif t.macd_hist_prev > 0 >= t.macd_hist:
            t.signals.append(ThresholdSignal(
                indicator="MACD hist flip", value=t.macd_hist, threshold="crossed <=0",
                direction="bearish", weight=1.25, note="momentum turning negative",
            ))
    if t.ema_9 is not None and t.ema_21 is not None:
        if t.ema_9 > t.ema_21:
            t.signals.append(ThresholdSignal(
                indicator="EMA9 vs EMA21", value=t.ema_9 - t.ema_21, threshold="EMA9>EMA21",
                direction="bullish", weight=0.75, note="short-term trend up",
            ))
        else:
            t.signals.append(ThresholdSignal(
                indicator="EMA9 vs EMA21", value=t.ema_9 - t.ema_21, threshold="EMA9<EMA21",
                direction="bearish", weight=0.75, note="short-term trend down",
            ))
    if t.sma_50 is not None and t.sma_200 is not None:
        if t.sma_50 > t.sma_200:
            t.signals.append(ThresholdSignal(
                indicator="Golden/Death cross", value=t.sma_50 - t.sma_200,
                threshold="SMA50>SMA200", direction="bullish", weight=1.5,
                note="long-term uptrend regime",
            ))
        else:
            t.signals.append(ThresholdSignal(
                indicator="Golden/Death cross", value=t.sma_50 - t.sma_200,
                threshold="SMA50<SMA200", direction="bearish", weight=1.5,
                note="long-term downtrend regime",
            ))
    if t.adx_14 is not None and t.adx_14 >= 25 and t.price_vs_ema200_pct is not None:
        direction = "bullish" if t.price_vs_ema200_pct > 0 else "bearish"
        t.signals.append(ThresholdSignal(
            indicator="ADX(14)", value=t.adx_14, threshold=">=25",
            direction=direction, weight=1.0,
            note=f"trend strength confirmed, price {'>' if direction=='bullish' else '<'} EMA200",
        ))
    return t


def _build_volatility(df: pd.DataFrame) -> Volatility:
    close, high, low = df["close"], df["high"], df["low"]
    v = Volatility()
    bu, bm, bl, bw = _bollinger(close, 20, 2.0)
    v.bb_upper, v.bb_middle, v.bb_lower, v.bb_width = _last(bu), _last(bm), _last(bl), _last(bw)
    ku, km, kl, kw = _keltner(high, low, close, 20, 2.0)
    v.keltner_upper, v.keltner_middle, v.keltner_lower, v.keltner_width = _last(ku), _last(km), _last(kl), _last(kw)
    atr = _atr(high, low, close, 14)
    v.atr_14 = _last(atr)
    last_close = _last(close)
    if v.atr_14 is not None and last_close:
        v.atr_pct = (v.atr_14 / last_close) * 100
    logret = np.log(close / close.shift(1))
    v.realized_vol_20d_ann = _last(logret.rolling(20).std(ddof=0) * math.sqrt(252) * 100)
    mean20 = close.rolling(20).mean()
    std20 = close.rolling(20).std(ddof=0)
    v.close_z_20 = _last((close - mean20) / std20.replace(0, np.nan))

    # Vol regime — bucket by percentile of ATR% over last 100 bars
    if v.atr_pct is not None:
        recent_atr_pct = (atr / close * 100).tail(100).dropna()
        if len(recent_atr_pct) >= 20:
            p33 = recent_atr_pct.quantile(0.33)
            p66 = recent_atr_pct.quantile(0.66)
            if v.atr_pct < p33:
                v.vol_regime = "low"
            elif v.atr_pct > p66:
                v.vol_regime = "high"
            else:
                v.vol_regime = "mid"

    # Signals
    if v.bb_width is not None and v.keltner_width is not None:
        if v.bb_width < v.keltner_width:
            v.signals.append(ThresholdSignal(
                indicator="BB/Keltner squeeze", value=v.bb_width - v.keltner_width,
                threshold="BBwidth<Keltnerwidth", direction="neutral", weight=1.0,
                note="squeeze — breakout imminent, direction TBD",
            ))
    if last_close is not None and v.bb_upper is not None and last_close > v.bb_upper:
        v.signals.append(ThresholdSignal(
            indicator="Bollinger breakout up", value=last_close - v.bb_upper,
            threshold="close>BBupper", direction="bullish", weight=1.0,
            note="upper-band expansion",
        ))
    if last_close is not None and v.bb_lower is not None and last_close < v.bb_lower:
        v.signals.append(ThresholdSignal(
            indicator="Bollinger breakdown", value=v.bb_lower - last_close,
            threshold="close<BBlower", direction="bearish", weight=1.0,
            note="lower-band expansion",
        ))
    if v.close_z_20 is not None:
        if v.close_z_20 < -2:
            v.signals.append(ThresholdSignal(
                indicator="Close z(20)", value=v.close_z_20, threshold="<-2",
                direction="bullish", weight=0.75, note="statistically stretched down",
            ))
        elif v.close_z_20 > 2:
            v.signals.append(ThresholdSignal(
                indicator="Close z(20)", value=v.close_z_20, threshold=">2",
                direction="bearish", weight=0.75, note="statistically stretched up",
            ))
    return v


def _build_volume(df: pd.DataFrame) -> Volume:
    high, low, close, vol = df["high"], df["low"], df["close"], df["volume"]
    out = Volume()
    obv = _obv(close, vol)
    out.obv = _last(obv)
    if len(obv) >= 11:
        out.obv_slope_10 = float((obv.iloc[-1] - obv.iloc[-11]) / 10.0)
    out.vwap = _last(_vwap(high, low, close, vol))
    out.cmf_20 = _last(_cmf(high, low, close, vol, 20))
    out.mfi_14 = _last(_mfi(high, low, close, vol, 14))
    v_mean = vol.rolling(20).mean()
    v_std = vol.rolling(20).std(ddof=0)
    out.volume_z_20 = _last((vol - v_mean) / v_std.replace(0, np.nan))

    if out.cmf_20 is not None:
        if out.cmf_20 > 0.1:
            out.signals.append(ThresholdSignal(
                indicator="CMF(20)", value=out.cmf_20, threshold=">0.1",
                direction="bullish", weight=0.75, note="money flowing in",
            ))
        elif out.cmf_20 < -0.1:
            out.signals.append(ThresholdSignal(
                indicator="CMF(20)", value=out.cmf_20, threshold="<-0.1",
                direction="bearish", weight=0.75, note="money flowing out",
            ))
    if out.mfi_14 is not None:
        if out.mfi_14 < 20:
            out.signals.append(ThresholdSignal(
                indicator="MFI(14)", value=out.mfi_14, threshold="<20",
                direction="bullish", weight=1.0, note="oversold (volume-weighted)",
            ))
        elif out.mfi_14 > 80:
            out.signals.append(ThresholdSignal(
                indicator="MFI(14)", value=out.mfi_14, threshold=">80",
                direction="bearish", weight=1.0, note="overbought (volume-weighted)",
            ))
    if out.volume_z_20 is not None and out.volume_z_20 > 2:
        last_close = _last(close)
        prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_close
        direction = "bullish" if last_close and prev_close and last_close > prev_close else "bearish"
        out.signals.append(ThresholdSignal(
            indicator="Volume spike z(20)", value=out.volume_z_20, threshold=">2",
            direction=direction, weight=0.75,
            note="unusual volume aligned with price direction",
        ))
    if out.obv_slope_10 is not None:
        if out.obv_slope_10 > 0:
            out.signals.append(ThresholdSignal(
                indicator="OBV slope(10)", value=out.obv_slope_10, threshold=">0",
                direction="bullish", weight=0.5, note="cumulative volume rising",
            ))
        elif out.obv_slope_10 < 0:
            out.signals.append(ThresholdSignal(
                indicator="OBV slope(10)", value=out.obv_slope_10, threshold="<0",
                direction="bearish", weight=0.5, note="cumulative volume falling",
            ))
    return out


def _build_structure(df: pd.DataFrame, atr_14: float | None) -> Structure:
    s = Structure()
    close, high, low = df["close"], df["high"], df["low"]

    if len(df) >= 2:
        prev = df.iloc[-2]
        P = (prev["high"] + prev["low"] + prev["close"]) / 3
        s.pivot_classic = {
            "P": float(P),
            "R1": float(2 * P - prev["low"]),
            "S1": float(2 * P - prev["high"]),
            "R2": float(P + (prev["high"] - prev["low"])),
            "S2": float(P - (prev["high"] - prev["low"])),
            "R3": float(prev["high"] + 2 * (P - prev["low"])),
            "S3": float(prev["low"] - 2 * (prev["high"] - P)),
        }

    for window in (20, 50, 200):
        if len(df) >= window:
            setattr(s, f"high_{window}d", float(high.tail(window).max()))
            setattr(s, f"low_{window}d", float(low.tail(window).min()))

    # Swing points from last 30-bar pivots
    tail = df.tail(30).reset_index(drop=True)
    if len(tail) >= 5:
        pivots_lows, pivots_highs = patterns._find_pivots(tail["close"], 2, 2)
        if pivots_highs:
            s.swing_high = float(tail["high"].iloc[pivots_highs[-1]])
        if pivots_lows:
            s.swing_low = float(tail["low"].iloc[pivots_lows[-1]])

    last_close = _last(close)
    # Nearest S/R = closest 20d/50d/swing level above/below last_close
    if last_close is not None:
        candidates_up: list[float] = [x for x in [s.high_20d, s.high_50d, s.swing_high,
                                                   s.pivot_classic.get("R1"),
                                                   s.pivot_classic.get("R2")] if x and x > last_close]
        candidates_dn: list[float] = [x for x in [s.low_20d, s.low_50d, s.swing_low,
                                                   s.pivot_classic.get("S1"),
                                                   s.pivot_classic.get("S2")] if x and x < last_close]
        if candidates_up:
            s.nearest_resistance = min(candidates_up)
        if candidates_dn:
            s.nearest_support = max(candidates_dn)
        if atr_14:
            if s.nearest_resistance is not None:
                s.resistance_distance_atr = (s.nearest_resistance - last_close) / atr_14
            if s.nearest_support is not None:
                s.support_distance_atr = (last_close - s.nearest_support) / atr_14

    if s.high_20d and last_close and last_close > s.high_20d * 0.999:
        s.break_of_structure = "bullish"
        s.signals.append(ThresholdSignal(
            indicator="20d high break", value=last_close, threshold=f">={s.high_20d:.2f}",
            direction="bullish", weight=1.25, note="breaks recent range",
        ))
    elif s.low_20d and last_close and last_close < s.low_20d * 1.001:
        s.break_of_structure = "bearish"
        s.signals.append(ThresholdSignal(
            indicator="20d low break", value=last_close, threshold=f"<={s.low_20d:.2f}",
            direction="bearish", weight=1.25, note="breaks recent range",
        ))
    return s


# Candlestick-pattern weights for the composite bull/bear ringer score
BULLISH_PATTERN_WEIGHTS = {
    "bullish_engulfing": 1.5,
    "hammer": 1.0,
    "morning_star": 1.75,
    "piercing_line": 1.0,
    "tweezer_bottom": 0.75,
    "three_white_soldiers": 2.0,
}
BEARISH_PATTERN_WEIGHTS = {
    "bearish_engulfing": 1.5,
    "shooting_star": 1.0,
    "evening_star": 1.75,
    "dark_cloud_cover": 1.0,
    "tweezer_top": 0.75,
    "three_black_crows": 2.0,
}


def _build_candles(df: pd.DataFrame) -> Candles:
    c = Candles()
    if len(df) < 3:
        return c
    last = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    o, h, l, cl = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    po, ph, pl, pc = float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])
    p2o, p2c = float(prev2["open"]), float(prev2["close"])

    c.doji = patterns.is_doji(o, h, l, cl)
    c.hammer = patterns.is_hammer(o, h, l, cl)
    c.shooting_star = patterns.is_shooting_star(o, h, l, cl)
    c.bullish_engulfing = patterns.is_bullish_engulfing(po, pc, o, cl)
    c.bearish_engulfing = patterns.is_bearish_engulfing(po, pc, o, cl)
    c.piercing_line = patterns.is_piercing_line(po, pc, o, cl)
    c.dark_cloud_cover = patterns.is_dark_cloud_cover(po, pc, o, cl)
    c.tweezer_bottom = patterns.is_tweezer_bottom(pl, l)
    c.tweezer_top = patterns.is_tweezer_top(ph, h)
    c.inside_bar = patterns.is_inside_bar(ph, pl, h, l)
    c.outside_bar = patterns.is_outside_bar(ph, pl, h, l)
    c.morning_star = patterns.is_morning_star(p2o, p2c, po, pc, o, cl)
    c.evening_star = patterns.is_evening_star(p2o, p2c, po, pc, o, cl)
    opens = df["open"].tail(3).tolist()
    closes = df["close"].tail(3).tolist()
    c.three_white_soldiers = patterns.is_three_white_soldiers(opens, closes)
    c.three_black_crows = patterns.is_three_black_crows(opens, closes)

    for name, w in BULLISH_PATTERN_WEIGHTS.items():
        if getattr(c, name):
            c.bull_ringer_score += w
            c.signals.append(ThresholdSignal(
                indicator=f"Candle: {name}", value=1.0, threshold="fired",
                direction="bullish", weight=w, note="bullish reversal pattern",
            ))
    for name, w in BEARISH_PATTERN_WEIGHTS.items():
        if getattr(c, name):
            c.bear_ringer_score += w
            c.signals.append(ThresholdSignal(
                indicator=f"Candle: {name}", value=1.0, threshold="fired",
                direction="bearish", weight=w, note="bearish reversal pattern",
            ))
    return c


def _build_divergences(df: pd.DataFrame) -> Divergences:
    d = Divergences()
    if len(df) < 30:
        return d
    close = df["close"]
    rsi = _rsi(close, 14)
    _, _, macd_hist = _macd(close)
    obv = _obv(close, df["volume"])

    for source, key in [(rsi, "rsi"), (macd_hist, "macd"), (obv, "obv")]:
        res = patterns.detect_divergences(close, source, lookback=60)
        if key == "rsi":
            d.regular_bullish_rsi = res["regular_bullish"]
            d.regular_bearish_rsi = res["regular_bearish"]
            d.hidden_bullish_rsi = res["hidden_bullish"]
            d.hidden_bearish_rsi = res["hidden_bearish"]
        elif key == "macd":
            d.regular_bullish_macd = res["regular_bullish"]
            d.regular_bearish_macd = res["regular_bearish"]
        elif key == "obv":
            d.regular_bullish_obv = res["regular_bullish"]
            d.regular_bearish_obv = res["regular_bearish"]
        d.strength_score += res["strength"]

        if res["regular_bullish"]:
            d.signals.append(ThresholdSignal(
                indicator=f"Divergence regular bull ({key.upper()})", value=None,
                threshold="price LL / osc HL", direction="bullish", weight=1.5,
                note="classic reversal setup",
            ))
        if res["regular_bearish"]:
            d.signals.append(ThresholdSignal(
                indicator=f"Divergence regular bear ({key.upper()})", value=None,
                threshold="price HH / osc LH", direction="bearish", weight=1.5,
                note="classic reversal setup",
            ))
    return d


# ── Public entry point ──────────────────────────────────────────────────────


def compute_snapshot(
    ohlcv_records: list[dict],
    ticker: str,
    as_of_date: str,
) -> IndicatorSnapshot:
    """Compute the full IndicatorSnapshot for the last bar of the OHLCV frame.

    The frame must be strictly ordered oldest→newest and contain OHLC + volume
    for each bar. Returns a snapshot even if some indicators are NaN (not
    enough history) — those fields are simply None and no signal fires.
    """
    df = _to_frame(ohlcv_records)
    if df.empty:
        return IndicatorSnapshot(
            ticker=ticker, as_of_date=as_of_date,
            last_close=0.0, last_volume=0.0, bars_used=0,
        )

    momentum = _build_momentum(df)
    trend = _build_trend(df)
    volatility = _build_volatility(df)
    volume = _build_volume(df)
    structure = _build_structure(df, atr_14=volatility.atr_14)
    candles = _build_candles(df)
    divergences = _build_divergences(df)

    raw_signals: list[ThresholdSignal] = (
        momentum.signals + trend.signals + volatility.signals
        + volume.signals + structure.signals + candles.signals + divergences.signals
    )
    # ── U6: apply regime-conditioned weight multipliers ───────────────
    all_signals = apply_regime_weights(raw_signals, trend.regime, volatility.vol_regime)
    # ── P12: apply ledger-learned per-signal accuracy multipliers ─────
    # A signal that's proven >60% hit rate over 30+ obs gets 1.5×; one
    # that's <45% over 60+ obs gets silenced (0×). Neutral otherwise.
    ledger = _ledger()
    all_signals = [
        sig.model_copy(update={
            "weight": sig.weight * get_weight_multiplier(sig.indicator, ledger),
        })
        for sig in all_signals
    ]
    # ── P14: apply freshness/age-decay multipliers ─────────────────────
    # Signals firing TODAY get +20% weight; each bar of staleness decays
    # exponentially (half-life ~5 bars). Stops us re-triggering on the
    # same lingering condition day after day.
    all_signals = apply_freshness(ticker, as_of_date, all_signals)
    composite_bull = sum(sig.weight for sig in all_signals if sig.direction == "bullish")
    composite_bear = sum(sig.weight for sig in all_signals if sig.direction == "bearish")

    snap = IndicatorSnapshot(
        ticker=ticker,
        as_of_date=as_of_date,
        last_close=float(df["close"].iloc[-1]),
        last_volume=float(df["volume"].iloc[-1]),
        bars_used=int(len(df)),
        momentum=momentum,
        trend=trend,
        volatility=volatility,
        volume=volume,
        structure=structure,
        candles=candles,
        divergences=divergences,
        composite_bull_score=composite_bull,
        composite_bear_score=composite_bear,
        net_bias_score=composite_bull - composite_bear,
        fired_signals=all_signals,
    )
    return snap
