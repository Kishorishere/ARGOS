"""Deep floorsheet analytics — flow, block trades, closing bias, turnover surge.

Everything here is DERIVED from the floorsheet + market-snapshot data we
already ingest daily. Zero new HTTP calls. All signals normalized to
[-1, +1] so they plug straight into the Bayesian ensemble.

Signals shipped:
  - `order_flow_imbalance(symbol, date)`  — buy-NPR minus sell-NPR fraction
  - `block_trade_score(symbol, date)`     — big-ticket institutional flow
  - `closing_bias(symbol, date)`          — close-to-high vs close-to-low
  - `turnover_surge(symbol, date)`        — vs 20-day average from history
  - `distinct_buyer_ratio(symbol, date)`  — breadth of demand
  - `wash_trade_ratio(symbol, date)`      — same broker on both sides (manipulation)
  - `circuit_breaker_proximity(symbol)`   — how close to +10%/-10% daily cap
  - `volume_price_divergence(symbol, ohlcv)` — up move on shrinking volume?
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

FLOORSHEET_DIR = Path(__file__).resolve().parents[2] / "data" / "floorsheet"
BLOCK_TRADE_NPR = 500_000    # NPR 5 lakh = institutional threshold


def _load_day_rows(d: str) -> list[dict]:
    """Load one day's floorsheet as raw dicts."""
    p = FLOORSHEET_DIR / f"{d}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def order_flow_imbalance(symbol: str, d: str) -> float | None:
    """Return (buy_side_amount - sell_side_amount) / total, in [-1, +1].

    A single trade goes into both sides (someone bought, someone sold).
    But we can measure which SIDE dominated by broker-turnover
    concentration: if the top-3 buying brokers control 70% of the NPR
    turnover, that's an institutional bid.

    Formula: (top3_buy_conc_pct - top3_sell_conc_pct) / 100
    → normalized to [-1, +1]
    """
    rows = [r for r in _load_day_rows(d) if r.get("symbol", "").upper() == symbol.upper()]
    if not rows:
        return None
    total_npr = sum(r.get("amount", 0) for r in rows)
    if total_npr == 0:
        return None
    buyer_amounts = Counter()
    seller_amounts = Counter()
    for r in rows:
        buyer_amounts[r["buyer_broker_id"]] += r.get("amount", 0)
        seller_amounts[r["seller_broker_id"]] += r.get("amount", 0)
    top3_buy = sum(v for _, v in buyer_amounts.most_common(3))
    top3_sell = sum(v for _, v in seller_amounts.most_common(3))
    imbalance = (top3_buy - top3_sell) / total_npr
    return round(max(-1.0, min(1.0, imbalance)), 3)


def block_trade_score(symbol: str, d: str,
                       threshold_npr: float = BLOCK_TRADE_NPR) -> dict | None:
    """Count big-ticket trades (>threshold NPR). Signed score based on
    whether block trades were dominated by concentrated BUYERS vs SELLERS.

    Returns:
      {n_blocks, block_npr_total, block_side_bias in [-1,+1]}
    """
    rows = [r for r in _load_day_rows(d) if r.get("symbol", "").upper() == symbol.upper()]
    if not rows:
        return None
    blocks = [r for r in rows if r.get("amount", 0) >= threshold_npr]
    if not blocks:
        return {"n_blocks": 0, "block_npr_total": 0.0, "block_side_bias": 0.0}
    total_block_npr = sum(r["amount"] for r in blocks)
    buyer_conc = Counter()
    seller_conc = Counter()
    for r in blocks:
        buyer_conc[r["buyer_broker_id"]] += r["amount"]
        seller_conc[r["seller_broker_id"]] += r["amount"]
    top_buyer = buyer_conc.most_common(1)[0][1]
    top_seller = seller_conc.most_common(1)[0][1]
    bias = (top_buyer - top_seller) / total_block_npr
    return {
        "n_blocks": len(blocks),
        "block_npr_total": round(total_block_npr, 0),
        "block_side_bias": round(max(-1.0, min(1.0, bias)), 3),
    }


def closing_bias(symbol: str) -> float | None:
    """Close-to-high vs close-to-low bias in [-1, +1].

    Uses the LIVE market snapshot (today's OHLC per symbol).
    +1 = closed at day's high (accumulation); -1 = closed at low (distribution).

    Formula: (close - low) / (high - low) → in [0, 1], centered to [-1, +1].
    """
    try:
        from argos.data.nepse_api import as_of_today
        items = as_of_today([symbol])
    except Exception:
        return None
    item = items.get(symbol.upper())
    if not item:
        return None
    high = item.get("high") or 0
    low = item.get("low") or 0
    close = item.get("ltp") or 0
    if high <= low:
        return 0.0
    # ltp can drift above high or below low intraday when the snapshot
    # captures a late tick before the OHLC fields refresh — clamp to
    # [0, 1] before centering so the signal stays in-range.
    normalized = max(0.0, min(1.0, (close - low) / (high - low)))
    return round(2 * normalized - 1, 3)  # -1..+1


def distinct_buyer_ratio(symbol: str, d: str) -> float | None:
    """Distinct buyer count / total trades. Ratio near 1 = broad demand,
    near 0 = one/two brokers doing all the buying (concentrated risk).
    """
    rows = [r for r in _load_day_rows(d) if r.get("symbol", "").upper() == symbol.upper()]
    if not rows:
        return None
    n_trades = len(rows)
    distinct_buyers = len({r["buyer_broker_id"] for r in rows})
    return round(distinct_buyers / max(n_trades, 1), 3)


def turnover_surge(symbol: str, d: str, ohlcv_records: list[dict],
                   lookback: int = 20) -> float | None:
    """Today's turnover vs `lookback`-day average, in log-ratio.

    Requires OHLCV history for the last N days.
    Returns log(today_turnover / avg_lookback) — signed.

    +1.0 = today's turnover is e^1 ≈ 2.7× normal
    +2.0 = 7.4× normal (attention magnet)
    Negative values = quieter than average.
    """
    try:
        from argos.data.nepse_api import as_of_today
        items = as_of_today([symbol])
    except Exception:
        return None
    item = items.get(symbol.upper())
    if not item:
        return None
    today_turnover = item.get("turnover") or 0
    if today_turnover <= 0 or len(ohlcv_records) < lookback:
        return None
    # Historical turnover = volume × close as proxy
    hist_turnover = [
        (r.get("volume") or 0) * (r.get("close") or 0)
        for r in ohlcv_records[-lookback:]
    ]
    avg = sum(hist_turnover) / len(hist_turnover)
    if avg <= 0:
        return None
    ratio = today_turnover / avg
    return round(math.log(max(ratio, 0.01)), 3)


def wash_trade_ratio(symbol: str, d: str) -> float | None:
    """Fraction of NPR turnover where the SAME broker sat on both sides.

    In a genuine market a broker rarely buys from itself — its clients
    are on both sides only by accident of the queue. When one broker is
    on 20%+ of both sides of a symbol's trades in a day, that's a
    smoking-gun signal of client-account manipulation (pump/dump prep,
    wash trading, spoofed volume).

    Returns fraction in [0, 1]: 0 = clean, 0.30+ = highly suspicious.
    None if no data.
    """
    rows = [r for r in _load_day_rows(d) if r.get("symbol", "").upper() == symbol.upper()]
    if not rows:
        return None
    total_npr = sum(r.get("amount", 0) for r in rows)
    if total_npr <= 0:
        return None
    self_cross_npr = sum(
        r.get("amount", 0)
        for r in rows
        if r.get("buyer_broker_id") and r.get("buyer_broker_id") == r.get("seller_broker_id")
    )
    return round(self_cross_npr / total_npr, 3)


def circuit_breaker_proximity(symbol: str, daily_limit_pct: float = 10.0,
                              caution_band: float = 1.5) -> float | None:
    """NEPSE enforces a ±10% daily circuit on individual stocks.

    Return a signed proximity signal for MEAN-REVERSION betting:
      +1.0 = stock near/at LOWER limit (-10%) → mean-revert edge is BULLISH
      -1.0 = stock near/at UPPER limit (+10%) → mean-revert edge is BEARISH
       0.0 = stock inside the normal band

    Rationale: hitting the daily limit means today's move is exhausted;
    the next session tends to fade. Traders "buy the down-limit, sell the
    up-limit" as a short-horizon edge. This signal is only for the 1d
    horizon — coefficients on 1w/1m are near zero.

    caution_band = how many percentage points inside the limit before we
    start firing. 1.5 means the signal ramps from 0 at ±8.5% to ±1 at ±10%.
    """
    try:
        from argos.data.nepse_api import as_of_today
        items = as_of_today([symbol])
    except Exception:
        return None
    item = items.get(symbol.upper())
    if not item:
        return None
    change_pct = item.get("changePercent")
    if change_pct is None:
        return None
    # Positive change → upper-limit approach → bearish mean-revert (negative signal)
    # Negative change → lower-limit approach → bullish mean-revert (positive signal)
    if change_pct >= daily_limit_pct - caution_band:
        excess = min(change_pct, daily_limit_pct) - (daily_limit_pct - caution_band)
        return round(-excess / caution_band, 3)
    if change_pct <= -(daily_limit_pct - caution_band):
        excess = max(change_pct, -daily_limit_pct) + (daily_limit_pct - caution_band)
        return round(-excess / caution_band, 3)
    return 0.0


def volume_price_divergence(symbol: str, ohlcv_records: list[dict],
                            lookback: int = 20) -> float | None:
    """Compare price change and volume change over the last few sessions.

    Rally on rising volume = healthy trend. Rally on FALLING volume =
    weak, fadable. And vice versa for declines.

    Formula:
      recent_price_change = (last close / close 5 sessions ago) - 1
      recent_vol_ratio    = mean(last 5 vol) / mean(prior 15 vol)
      divergence = -sign(recent_price_change) * (1 - min(recent_vol_ratio, 2)) * 0.5
                   IF price and volume disagree

    Returns [-1, +1]:
      +1 = price fell on light volume → likely bounce (bullish divergence)
      -1 = price rose on light volume → likely fade (bearish divergence)
       0 = price + volume aligned (nothing to fade)
    """
    if len(ohlcv_records) < lookback:
        return None
    closes = [r.get("close") or 0 for r in ohlcv_records[-lookback:]]
    vols = [r.get("volume") or 0 for r in ohlcv_records[-lookback:]]
    if closes[-6] <= 0 or not all(v >= 0 for v in vols):
        return None
    recent_price_change = (closes[-1] / closes[-6]) - 1  # last 5 sessions
    recent_vol_avg = sum(vols[-5:]) / 5
    prior_vol_avg = sum(vols[-lookback:-5]) / max(len(vols) - 5, 1)
    if prior_vol_avg <= 0:
        return None
    vol_ratio = recent_vol_avg / prior_vol_avg
    # Alignment: same sign as price change means trend is confirmed
    # Divergence: price up but volume drying up → weak rally → fade signal
    if abs(recent_price_change) < 0.01:  # ignore flat weeks
        return 0.0
    if vol_ratio >= 1.0:
        # Volume confirms — no divergence
        return 0.0
    # Volume < prior avg while price moved: divergence, sign OPPOSITE of move
    divergence_magnitude = 1.0 - min(vol_ratio, 1.0)  # 0..1
    sign = -1.0 if recent_price_change > 0 else 1.0    # fade the move
    return round(sign * min(divergence_magnitude, 1.0), 3)


def flow_summary(symbol: str, d: str, ohlcv_records: list[dict] | None = None) -> dict:
    """Combined flow view for one symbol on one day."""
    return {
        "symbol": symbol.upper(),
        "date": d,
        "order_flow_imbalance": order_flow_imbalance(symbol, d),
        "block_trades": block_trade_score(symbol, d),
        "closing_bias": closing_bias(symbol),
        "distinct_buyer_ratio": distinct_buyer_ratio(symbol, d),
        "turnover_surge_log": turnover_surge(symbol, d, ohlcv_records or []),
        "wash_trade_ratio": wash_trade_ratio(symbol, d),
        "circuit_breaker_proximity": circuit_breaker_proximity(symbol),
        "volume_price_divergence": volume_price_divergence(symbol, ohlcv_records or []),
    }
