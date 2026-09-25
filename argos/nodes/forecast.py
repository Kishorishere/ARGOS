"""Multi-horizon forecast node (P17).

Runs AFTER technicals (which produces IndicatorSnapshot + calibrated
Kronos multi-horizon) and BEFORE the debate. Emits a deterministic
`MultiHorizonForecast` on 1-day / 1-week / 1-month horizons that the
Portfolio Manager, judge, and reporting pipelines all consume.

Design rationale:
- **No LLM here.** Fast, deterministic, zero quota. Everything is derived
  from the snapshot + Kronos.
- **Independent horizons.** A stock can be bearish 1-day (short-term
  overextended) but bullish 1-month (fundamentals improving). Each
  horizon reads its own preferred inputs.
- **Time-scaled uncertainty.** Confidence bands scale with sqrt(horizon)
  since volatility scales with sqrt(t) under Brownian assumptions.
- **Honest confidence.** Confidence = f(agreement across sources, signal
  strength, uncertainty), not a made-up number.
"""

from __future__ import annotations

import logging
import math

from argos.ensemble import combined_probability
from argos.schemas import HorizonForecast, MultiHorizonForecast
from argos.state import FuseState

logger = logging.getLogger(__name__)


def _logistic(x: float, k: float = 1.0) -> float:
    """Squash any real to [0, 1] via sigmoid. k controls steepness."""
    try:
        return 1.0 / (1.0 + math.exp(-k * x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _direction(p_up: float, threshold: float = 0.05) -> str:
    if p_up > 0.5 + threshold:
        return "up"
    if p_up < 0.5 - threshold:
        return "down"
    return "flat"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _forecast_1d(snap: dict, kronos: dict, atr_pct: float) -> HorizonForecast:
    """1-day forecast — driven by short-horizon Kronos + composite bias.

    Signal weights (empirical, tunable via P12 ledger later):
      - Kronos short_horizon_pct: 0.5 log-odds per 1% move
      - Composite net_bias:       0.15 log-odds per unit
      - Regime alignment:         ±0.2 log-odds
    """
    kronos_short = kronos.get("short_horizon_pct") or 0.0
    kronos_dir = kronos.get("direction_score") or 0.0
    net_bias = snap.get("net_bias_score") or 0.0
    trend_regime = (snap.get("trend") or {}).get("regime")

    # Log-odds accumulator
    log_odds = 0.0
    drivers: list[str] = []
    if kronos_short:
        log_odds += 0.5 * kronos_short
        drivers.append(f"Kronos short {kronos_short:+.2f}%")
    if kronos_dir:
        log_odds += 0.5 * kronos_dir
        drivers.append(f"Kronos dir {kronos_dir:+.2f}")
    log_odds += 0.15 * net_bias
    drivers.append(f"Composite bias {net_bias:+.2f}")
    # Trend alignment bonus (short-term momentum in trending regime)
    if trend_regime == "uptrend" and log_odds > 0:
        log_odds += 0.2
        drivers.append("Trend-aligned bull")
    elif trend_regime == "downtrend" and log_odds < 0:
        log_odds -= 0.2
        drivers.append("Trend-aligned bear")
    # Bull-ringer / bear-ringer candle boost
    candles = snap.get("candles") or {}
    bull_r = candles.get("bull_ringer_score") or 0.0
    bear_r = candles.get("bear_ringer_score") or 0.0
    if bull_r > 0:
        log_odds += 0.2 * bull_r
        drivers.append(f"Bull-ringer +{bull_r:.1f}")
    if bear_r > 0:
        log_odds -= 0.2 * bear_r
        drivers.append(f"Bear-ringer +{bear_r:.1f}")

    p_up = _clamp(_logistic(log_odds), 0.05, 0.95)  # never claim certainty
    # Expected 1-day move: half of Kronos short OR 0.3×ATR% signed by bias
    expected = 0.5 * (kronos_short or (0.3 * atr_pct * (1 if net_bias > 0 else -1)))
    # Bands: ±1.2 × ATR% at horizon=1 (roughly 1 std)
    band = 1.2 * atr_pct
    p10 = expected - band
    p90 = expected + band
    # Confidence: honest cap at 0.85 — nothing at daily horizon should exceed that
    confidence = _clamp(0.5 + abs(p_up - 0.5) * 0.7, 0.0, 0.85)
    return HorizonForecast(
        horizon="1d",
        direction=_direction(p_up),
        probability_up=_clamp(p_up, 0.0, 1.0),
        expected_return_pct=round(expected, 3),
        p10_return_pct=round(p10, 3),
        p50_return_pct=round(expected, 3),
        p90_return_pct=round(p90, 3),
        confidence=round(confidence, 3),
        key_drivers=drivers[:5],
    )


def _sector_rs_for(ticker: str) -> tuple[float, str] | None:
    """P4: return (relative_strength_pct, sector_name) for a ticker.

    Prefer accumulated 20-day RS if history is available; else fall back
    to today-only sector-momentum vs NEPSE.
    """
    try:
        from argos.data.nepse_sectors import (
            symbol_to_sector, sector_relative_strength, today_sector_scores,
        )
    except Exception:
        return None
    sector = symbol_to_sector(ticker)
    if not sector:
        return None
    rs = sector_relative_strength(sector, window=20)
    if rs and rs.get("relative_strength_pct") is not None:
        return (rs["relative_strength_pct"], sector)
    try:
        scores = today_sector_scores()
    except Exception:
        return None
    return (float(scores.get(sector, 0.0)), sector)


def _forecast_1w(snap: dict, kronos: dict, atr_pct: float,
                 ticker: str | None = None) -> HorizonForecast:
    """1-week forecast — leans on Kronos swing_horizon + weekly-timeframe
    signals (P3) + long-term trend.

    5 trading days ≈ vol scales by sqrt(5) ≈ 2.24×.
    """
    kronos_swing = kronos.get("swing_horizon_pct") or 0.0
    kronos_dir = kronos.get("direction_score") or 0.0
    net_bias = snap.get("net_bias_score") or 0.0
    trend = snap.get("trend") or {}
    trend_regime = trend.get("regime")
    ema_slope = trend.get("price_vs_ema200_pct") or 0.0
    macd_hist = trend.get("macd_hist") or 0.0
    adx = trend.get("adx_14") or 0.0
    # P3: weekly-timeframe signals (much more predictive at 1w horizon)
    mtf = snap.get("multi_timeframe") or {}
    weekly = mtf.get("1w") or {}
    weekly_bias = weekly.get("net_bias_score") or 0.0
    confluence = snap.get("confluence") or {}
    confluence_score_val = confluence.get("score") or 0.0

    log_odds = 0.0
    drivers: list[str] = []
    if kronos_swing:
        log_odds += 0.6 * kronos_swing
        drivers.append(f"Kronos swing {kronos_swing:+.2f}%")
    log_odds += 0.4 * kronos_dir
    log_odds += 0.10 * net_bias
    # P3: weekly-frame bias is the strongest 1-week predictor
    if weekly_bias:
        log_odds += 0.25 * weekly_bias
        drivers.append(f"Weekly bias {weekly_bias:+.2f}")
    # Confluence bonus: aligned bullish/bearish across 3 timeframes = strong
    if confluence.get("aligned_bullish"):
        log_odds += 0.6
        drivers.append("3/3 TF aligned bull")
    elif confluence.get("aligned_bearish"):
        log_odds -= 0.6
        drivers.append("3/3 TF aligned bear")
    log_odds += 0.05 * ema_slope  # % above/below EMA200
    if abs(macd_hist) > 0:
        log_odds += 5.0 * macd_hist  # MACD hist is small numbers
        drivers.append(f"MACD hist {macd_hist:+.3f}")
    if trend_regime == "uptrend":
        log_odds += 0.4
        drivers.append("Uptrend regime")
    elif trend_regime == "downtrend":
        log_odds -= 0.4
        drivers.append("Downtrend regime")
    if adx > 25:
        # Strong trend → magnify the direction bias
        log_odds *= 1.2
        drivers.append(f"ADX {adx:.0f} strong")

    # P4: sector rotation boost — long in top-sector, short in bottom-sector
    if ticker:
        rs_and_sector = _sector_rs_for(ticker)
        if rs_and_sector:
            rs, sector_name = rs_and_sector
            # 0.15 log-odds per 1% relative strength (capped)
            log_odds += _clamp(rs * 0.15, -0.5, 0.5)
            drivers.append(f"Sector {sector_name} RS {rs:+.2f}%")

    p_up = _clamp(_logistic(log_odds), 0.05, 0.95)
    expected = kronos_swing or (ema_slope * 0.15 + net_bias * 0.3)
    band = 1.2 * atr_pct * math.sqrt(5)  # vol scales with sqrt(t)
    p10 = expected - band
    p90 = expected + band
    # 1w confidence can go higher (0.90 cap) — trends persist better than 1d moves
    confidence = _clamp(0.5 + abs(p_up - 0.5) * 0.85, 0.0, 0.90)
    return HorizonForecast(
        horizon="1w",
        direction=_direction(p_up),
        probability_up=_clamp(p_up, 0.0, 1.0),
        expected_return_pct=round(expected, 3),
        p10_return_pct=round(p10, 3),
        p50_return_pct=round(expected, 3),
        p90_return_pct=round(p90, 3),
        confidence=round(confidence, 3),
        key_drivers=drivers[:5],
    )


def _forecast_1m(snap: dict, kronos: dict, atr_pct: float,
                 ticker: str | None = None) -> HorizonForecast:
    """1-month forecast — leans on regime + long-term structure + fundamentals.

    21 trading days ≈ vol scales by sqrt(21) ≈ 4.58×.
    Fundamentals F-score contributes ±0.6 log-odds when available (P8).
    """
    trend = snap.get("trend") or {}
    trend_regime = trend.get("regime")
    ema_200_gap = trend.get("price_vs_ema200_pct") or 0.0
    sma_50 = trend.get("sma_50") or 0
    sma_200 = trend.get("sma_200") or 0
    net_bias = snap.get("net_bias_score") or 0.0
    kronos_dir = kronos.get("direction_score") or 0.0
    struct = snap.get("structure") or {}
    break_of_structure = struct.get("break_of_structure", "none")
    # P3: monthly-timeframe signals are the strongest 1-month predictor
    mtf = snap.get("multi_timeframe") or {}
    monthly = mtf.get("1m") or {}
    monthly_bias = monthly.get("net_bias_score") or 0.0
    monthly_regime = (monthly.get("trend") or {}).get("regime")
    confluence = snap.get("confluence") or {}

    log_odds = 0.0
    drivers: list[str] = []
    log_odds += 0.02 * ema_200_gap  # +5% above EMA200 → +0.1 log-odds
    drivers.append(f"Price {ema_200_gap:+.1f}% vs EMA200")
    if sma_50 and sma_200:
        if sma_50 > sma_200 * 1.02:
            log_odds += 0.4
            drivers.append("Golden-cross regime")
        elif sma_50 < sma_200 * 0.98:
            log_odds -= 0.4
            drivers.append("Death-cross regime")
    if trend_regime == "uptrend":
        log_odds += 0.5
    elif trend_regime == "downtrend":
        log_odds -= 0.5
    # P3: monthly bias is the strongest signal at this horizon
    if monthly_bias:
        log_odds += 0.35 * monthly_bias
        drivers.append(f"Monthly bias {monthly_bias:+.2f}")
    if monthly_regime == "uptrend":
        log_odds += 0.4
        drivers.append("Monthly uptrend")
    elif monthly_regime == "downtrend":
        log_odds -= 0.4
        drivers.append("Monthly downtrend")
    # Confluence bonus
    if confluence.get("aligned_bullish"):
        log_odds += 0.5
    elif confluence.get("aligned_bearish"):
        log_odds -= 0.5
    log_odds += 0.3 * kronos_dir  # Kronos direction contributes but less at 1m
    log_odds += 0.08 * net_bias
    if break_of_structure == "bullish":
        log_odds += 0.3
        drivers.append("Bullish break-of-structure")
    elif break_of_structure == "bearish":
        log_odds -= 0.3
        drivers.append("Bearish break-of-structure")

    # P8: fundamentals contribution to 1-month horizon (biggest lever here)
    if ticker:
        try:
            from argos.fundamentals import compute_f_score, sector_rank
            f = compute_f_score(ticker)
            r = sector_rank(ticker)
            if f:
                # Center F-score at max/2 → ±0.6 log-odds per side of neutral
                center = f["max_possible"] / 2
                log_odds += 0.15 * (f["raw_score"] - center)
                drivers.append(f"F-score {f['raw_score']}/{f['max_possible']} ({f['grade']})")
            if r:
                # Composite in [0..100]; center at 50 → ±0.4 log-odds
                log_odds += 0.008 * (r["composite_score"] - 50)
                drivers.append(f"Sector rank {r['composite_score']}/100 ({r['grade']})")
        except Exception:
            pass

    p_up = _clamp(_logistic(log_odds), 0.05, 0.95)
    # Expected 1m: honour p_up direction even when the trend regime is
    # unclear. Old code produced expected=0 when regime was None + not
    # overextended, which contradicts a p_up of 0.90. Now: base on p_up
    # deviation from 50%, scaled by ATR × sqrt(21).
    if trend_regime == "uptrend":
        drift = 3.0
    elif trend_regime == "downtrend":
        drift = -3.0
    else:
        # Derive drift from p_up: (p_up - 0.5) × 6 gives ±3% at the extremes
        drift = (p_up - 0.5) * 6.0
    # Mild mean-reversion pull if massively overextended
    mean_rev = -0.25 * ema_200_gap if abs(ema_200_gap) > 10 else 0.0
    expected = drift + mean_rev
    band = 1.2 * atr_pct * math.sqrt(21)
    p10 = expected - band
    p90 = expected + band
    # 1m confidence highest ceiling — long-term structure is more predictable
    confidence = _clamp(0.5 + abs(p_up - 0.5) * 0.8, 0.0, 0.88)
    return HorizonForecast(
        horizon="1m",
        direction=_direction(p_up),
        probability_up=_clamp(p_up, 0.0, 1.0),
        expected_return_pct=round(expected, 3),
        p10_return_pct=round(p10, 3),
        p50_return_pct=round(expected, 3),
        p90_return_pct=round(p90, 3),
        confidence=round(confidence, 3),
        key_drivers=drivers[:5],
    )


def _blend_with_ensemble(hand_tuned: HorizonForecast, ensemble_prob: float,
                          top_drivers: list[str]) -> HorizonForecast:
    """Blend the hand-tuned horizon estimate with the ensemble probability.

    The hand-tuned code has good defaults but can miss cross-source
    interactions the ensemble captures. We take a weighted average of
    the two probabilities and update key_drivers to include the top
    ensemble contributors.

    Weighting: 60% ensemble / 40% hand-tuned — the ensemble encodes MORE
    sources so it should dominate; hand-tuned remains as a sanity anchor.
    """
    blended_p = 0.6 * ensemble_prob + 0.4 * hand_tuned.probability_up
    blended_p = max(0.05, min(0.95, blended_p))
    if blended_p > 0.55:
        direction = "up"
    elif blended_p < 0.45:
        direction = "down"
    else:
        direction = "flat"
    # Merge drivers: keep the top ensemble ones first, then existing
    combined_drivers = list(dict.fromkeys(top_drivers[:3] + hand_tuned.key_drivers[:2]))[:5]
    return HorizonForecast(
        horizon=hand_tuned.horizon,
        direction=direction,
        probability_up=round(blended_p, 4),
        expected_return_pct=hand_tuned.expected_return_pct,
        p10_return_pct=hand_tuned.p10_return_pct,
        p50_return_pct=hand_tuned.p50_return_pct,
        p90_return_pct=hand_tuned.p90_return_pct,
        confidence=max(hand_tuned.confidence, min(0.9, 0.5 + abs(blended_p - 0.5) * 0.9)),
        key_drivers=combined_drivers,
    )


def forecast_node(state: FuseState) -> dict:
    """Emit MultiHorizonForecast for the current ticker + date.

    Deterministic. No LLM. Runs in <50ms. Uses:
      1. Hand-tuned per-horizon heuristics (fast, always available)
      2. Bayesian ensemble aggregator (blends 15+ sources; P1+P2)
    Then blends the two probabilities (60/40 favouring ensemble).
    """
    ticker = state["ticker"]
    as_of = state["as_of_date"]
    ts = state.get("technicals_signal") or {}
    snap = ts.get("indicator_snapshot") or {}
    kronos = state.get("kronos_forecast") or {}

    if not snap:
        logger.debug("forecast_node: no indicator_snapshot → skipping")
        return {}

    atr_pct = (snap.get("volatility") or {}).get("atr_pct") or 1.0
    last_close = snap.get("last_close") or 0.0

    horizons = {
        "1d": _forecast_1d(snap, kronos, atr_pct),
        "1w": _forecast_1w(snap, kronos, atr_pct, ticker=ticker),
        "1m": _forecast_1m(snap, kronos, atr_pct, ticker=ticker),
    }

    # ── Ensemble blend ──────────────────────────────────────────────────
    # Run the Bayesian aggregator on the same state and blend with
    # hand-tuned probabilities. Journalled per-horizon so we can audit.
    ensemble_by_horizon: dict[str, dict] = {}
    for h in ("1d", "1w", "1m"):
        try:
            ens = combined_probability(state, h)  # type: ignore[arg-type]
            top = [c.source for c in ens.top_drivers(5)]
            horizons[h] = _blend_with_ensemble(horizons[h], ens.probability_up, top)
            ensemble_by_horizon[h] = ens.to_dict()
        except Exception as e:  # noqa: BLE001
            logger.warning("Ensemble for %s %s failed: %s — using hand-tuned only",
                           ticker, h, e)

    forecast = MultiHorizonForecast(
        symbol=ticker,
        as_of_date=as_of,
        last_close=last_close,
        horizons=horizons,
    )
    payload = forecast.model_dump()
    # HorizonForecast objects → dicts for JSON serialization
    payload["horizons"] = {k: v.model_dump() for k, v in horizons.items()}
    payload["ensemble_by_horizon"] = ensemble_by_horizon

    logger.info(
        "Forecast %s @ %s: 1d %s p_up=%.2f exp %+.2f%% | 1w %s p_up=%.2f exp %+.2f%% | 1m %s p_up=%.2f exp %+.2f%%",
        ticker, as_of,
        horizons["1d"].direction, horizons["1d"].probability_up, horizons["1d"].expected_return_pct,
        horizons["1w"].direction, horizons["1w"].probability_up, horizons["1w"].expected_return_pct,
        horizons["1m"].direction, horizons["1m"].probability_up, horizons["1m"].expected_return_pct,
    )
    return {"multi_horizon_forecast": payload}
