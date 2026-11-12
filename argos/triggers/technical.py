"""Technical triggers — rules a chart-watching trader would react to.

Every check in this module takes a ticker + as_of, pulls the OHLCV window
from the same loader the pipeline uses, and returns zero or more triggers.
Rules are intentionally boring (textbook): the point isn't to invent new
indicators, it's to make the system react to the same signals a working
trader already respects.

Rules implemented:
  rsi_cross_30     — RSI(14) crosses up through 30 (oversold → bull hint)
  rsi_cross_70     — RSI(14) crosses down through 70 (overbought → bear hint)
  donchian_break   — close breaks 20-day high or low
  bb_squeeze       — Bollinger bandwidth hits a 90d low (compression)
  volume_spike     — today's vol > 2x 20-day average (direction = sign of return)
  ma_cross_golden  — MA(50) crosses above MA(200)
  ma_cross_death   — MA(50) crosses below MA(200)

Each check is independent; `check_all(ticker, as_of)` runs them and returns
the union. All checks fail silently (return []) if the OHLCV window is
too short or load fails — triggers are advisory, not load-bearing.
"""

from __future__ import annotations

import logging

from argos.triggers.base import Trigger

logger = logging.getLogger(__name__)


def _load_closes(ticker: str, as_of: str, lookback_days: int = 365) -> list[dict] | None:
    """Pull the OHLCV window for the trigger math. Uses the same loader as
    the pipeline so backtesting and live modes stay consistent."""
    try:
        from argos.data.ohlcv_loader import load_ohlcv
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        allow_today = as_of == today
        window = load_ohlcv(ticker, as_of, lookback_days=lookback_days, allow_today=allow_today)
        return window.get("ohlcv") or None
    except Exception as e:  # noqa: BLE001
        logger.debug("trigger: _load_closes(%s @ %s) failed: %s", ticker, as_of, e)
        return None


def _sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        out.append(s / period if i >= period - 1 else None)
    return out


def _stdev(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        mu = sum(window) / period
        var = sum((x - mu) ** 2 for x in window) / period
        out.append(var**0.5)
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    avg_g = 0.0
    avg_l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            avg_g += d
        else:
            avg_l -= d
    avg_g /= period
    avg_l /= period
    first_rs = 100.0 if avg_l == 0 else avg_g / avg_l
    out[period] = 100 - 100 / (1 + first_rs)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_g = (avg_g * (period - 1) + gain) / period
        avg_l = (avg_l * (period - 1) + loss) / period
        rs = 100.0 if avg_l == 0 else avg_g / avg_l
        out[i] = 100 - 100 / (1 + rs)
    return out


# ── Individual rules ────────────────────────────────────────────────────────

def _rsi_crosses(ticker: str, as_of: str, bars: list[dict]) -> list[Trigger]:
    closes = [b["close"] for b in bars]
    rsi = _rsi(closes, 14)
    if len(rsi) < 2 or rsi[-1] is None or rsi[-2] is None:
        return []
    prev, now = rsi[-2], rsi[-1]
    out: list[Trigger] = []
    assert prev is not None and now is not None
    if prev < 30 <= now:
        out.append(Trigger(ticker, as_of, "rsi_cross_30", "bullish", float(now),
                           f"RSI(14) crossed up through 30 ({prev:.1f} → {now:.1f})", "technical"))
    if prev > 70 >= now:
        out.append(Trigger(ticker, as_of, "rsi_cross_70", "bearish", float(now),
                           f"RSI(14) crossed down through 70 ({prev:.1f} → {now:.1f})", "technical"))
    return out


def _donchian_break(ticker: str, as_of: str, bars: list[dict]) -> list[Trigger]:
    if len(bars) < 22:
        return []
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    hi20 = max(highs[-21:-1])    # prior 20-day high, excluding today
    lo20 = min(lows[-21:-1])
    today_close = closes[-1]
    out: list[Trigger] = []
    if today_close > hi20:
        out.append(Trigger(ticker, as_of, "donchian_break_up", "bullish",
                           (today_close / hi20 - 1) * 100,
                           f"Close {today_close:.2f} broke 20-day high {hi20:.2f}",
                           "technical"))
    if today_close < lo20:
        out.append(Trigger(ticker, as_of, "donchian_break_down", "bearish",
                           (today_close / lo20 - 1) * 100,
                           f"Close {today_close:.2f} broke 20-day low {lo20:.2f}",
                           "technical"))
    return out


def _bb_squeeze(ticker: str, as_of: str, bars: list[dict]) -> list[Trigger]:
    if len(bars) < 110:
        return []
    closes = [b["close"] for b in bars]
    ma20 = _sma(closes, 20)
    sd20 = _stdev(closes, 20)
    width: list[float | None] = []
    for m, s in zip(ma20, sd20):
        width.append((2 * s) / m if m and s else None)
    valid = [w for w in width[-90:] if w is not None]
    today_w = width[-1]
    if today_w is None or not valid:
        return []
    # Fire when today's bandwidth is in the lowest 10% of the last 90 days
    threshold = sorted(valid)[int(len(valid) * 0.1)]
    if today_w <= threshold:
        return [Trigger(ticker, as_of, "bb_squeeze", "neutral", float(today_w),
                        f"Bollinger bandwidth {today_w:.4f} at 90-day low (p10={threshold:.4f})",
                        "technical")]
    return []


def _volume_spike(ticker: str, as_of: str, bars: list[dict]) -> list[Trigger]:
    if len(bars) < 22:
        return []
    vols = [b["volume"] for b in bars]
    avg20 = sum(vols[-21:-1]) / 20
    today_v = vols[-1]
    if avg20 <= 0 or today_v < 2 * avg20:
        return []
    today_ret = bars[-1]["close"] - bars[-2]["close"]
    direction = "bullish" if today_ret > 0 else "bearish" if today_ret < 0 else "neutral"
    ratio = today_v / avg20
    return [Trigger(ticker, as_of, "volume_spike", direction, float(ratio),
                    f"Volume {today_v:,} = {ratio:.2f}× 20-day avg "
                    f"(close Δ {today_ret:+.2f})",
                    "technical")]


def _ma_cross(ticker: str, as_of: str, bars: list[dict]) -> list[Trigger]:
    if len(bars) < 202:
        return []
    closes = [b["close"] for b in bars]
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    if ma50[-2] is None or ma50[-1] is None or ma200[-2] is None or ma200[-1] is None:
        return []
    prev_diff = ma50[-2] - ma200[-2]
    now_diff = ma50[-1] - ma200[-1]
    assert prev_diff is not None and now_diff is not None
    if prev_diff <= 0 < now_diff:
        return [Trigger(ticker, as_of, "ma_cross_golden", "bullish", float(now_diff),
                        f"MA50 crossed above MA200 ({ma50[-1]:.2f} vs {ma200[-1]:.2f})",
                        "technical")]
    if prev_diff >= 0 > now_diff:
        return [Trigger(ticker, as_of, "ma_cross_death", "bearish", float(now_diff),
                        f"MA50 crossed below MA200 ({ma50[-1]:.2f} vs {ma200[-1]:.2f})",
                        "technical")]
    return []


# ── Public entry point ─────────────────────────────────────────────────────

def check(ticker: str, as_of: str) -> list[Trigger]:
    bars = _load_closes(ticker, as_of)
    if not bars:
        return []
    out: list[Trigger] = []
    for fn in (_rsi_crosses, _donchian_break, _bb_squeeze, _volume_spike, _ma_cross):
        try:
            out.extend(fn(ticker, as_of, bars))
        except Exception as e:  # noqa: BLE001
            logger.debug("trigger.technical.%s(%s@%s) failed: %s", fn.__name__, ticker, as_of, e)
    return out
