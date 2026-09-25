"""Ensemble aggregation via Bayesian log-odds combination.

Every signal source (indicator composite, Kronos, MTF confluence, LLM
debate, judge, sector RS, macro, breadth, F-score) contributes a signed
log-odds term:

    logit_i = coef_i * strength_i        # signed, small numbers
    log_odds_total = sum(logit_i)
    prob_up = sigmoid(log_odds_total)

Coefficients are per-source AND per-horizon (short-term sources like
Kronos short and 1d MACD dominate 1d; long-term sources like F-score
and monthly regime dominate 1m).

This unifies what the forecast_node did with a bunch of if-statements
into one auditable, tunable structure. Every contribution is journalled
so you can see which source moved the final probability.

Empirical coefficients below are conservative defaults; the ledger-tuning
loop refines them over time (P12/P1 combined).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

Horizon = Literal["1d", "1w", "1m"]

# Per-source × per-horizon log-odds coefficients. Positive coefficient
# means larger source strength → higher probability_up.
# These are Bayesian log-odds units; each ~0.5 = one factor of e ≈ 65%.
COEFFICIENTS: dict[str, dict[Horizon, float]] = {
    "indicator_net_bias":  {"1d": 0.15, "1w": 0.10, "1m": 0.08},
    "kronos_direction":    {"1d": 0.60, "1w": 0.45, "1m": 0.30},
    "kronos_short_pct":    {"1d": 0.30, "1w": 0.15, "1m": 0.05},
    "kronos_swing_pct":    {"1d": 0.10, "1w": 0.60, "1m": 0.30},
    "mtf_confluence":      {"1d": 0.20, "1w": 0.45, "1m": 0.35},
    "weekly_bias":         {"1d": 0.05, "1w": 0.30, "1m": 0.15},
    "monthly_bias":        {"1d": 0.00, "1w": 0.15, "1m": 0.35},
    "hurst_regime":        {"1d": 0.15, "1w": 0.20, "1m": 0.10},
    "trend_regime":        {"1d": 0.20, "1w": 0.40, "1m": 0.50},
    "sector_rs":           {"1d": 0.05, "1w": 0.15, "1m": 0.25},
    "breadth_ad":          {"1d": 0.25, "1w": 0.10, "1m": 0.05},
    "macro_regime":        {"1d": 0.20, "1w": 0.30, "1m": 0.40},
    "bull_conf":           {"1d": 0.35, "1w": 0.25, "1m": 0.15},
    "bear_conf":           {"1d": 0.35, "1w": 0.25, "1m": 0.15},
    "judge":               {"1d": 0.40, "1w": 0.35, "1m": 0.25},
    "f_score":             {"1d": 0.05, "1w": 0.20, "1m": 0.40},
    "sector_rank":         {"1d": 0.05, "1w": 0.20, "1m": 0.35},
    "bull_ringer":         {"1d": 0.30, "1w": 0.20, "1m": 0.05},
    "bear_ringer":         {"1d": 0.30, "1w": 0.20, "1m": 0.05},
    "candle_pattern":      {"1d": 0.20, "1w": 0.10, "1m": 0.03},
    "divergence":          {"1d": 0.15, "1w": 0.25, "1m": 0.10},
    # P9: smart money / floorsheet
    "smart_money":         {"1d": 0.20, "1w": 0.40, "1m": 0.25},
    # P10: Nepali-language sentiment
    "nepali_sentiment":    {"1d": 0.35, "1w": 0.25, "1m": 0.15},
    # P21: broker reputation of today's top buyer/seller
    "broker_edge_buyer":   {"1d": 0.30, "1w": 0.35, "1m": 0.20},
    "broker_edge_seller":  {"1d": 0.25, "1w": 0.30, "1m": 0.15},
    # P22: order-flow imbalance (buy-NPR minus sell-NPR concentration)
    "order_flow_imbalance":{"1d": 0.45, "1w": 0.30, "1m": 0.15},
    # P22b: block-trade side bias (institutional flow)
    "block_trade_bias":    {"1d": 0.40, "1w": 0.35, "1m": 0.20},
    # P24: intraday close-to-high vs close-to-low
    "closing_bias":        {"1d": 0.35, "1w": 0.20, "1m": 0.10},
    # P25: turnover surge vs 20d average (log ratio)
    "turnover_surge":      {"1d": 0.20, "1w": 0.15, "1m": 0.10},
    # P23: distinct-buyer breadth (near 1 = broad demand)
    "buyer_breadth":       {"1d": 0.15, "1w": 0.10, "1m": 0.05},
    # P26: wash-trade (same broker on both sides) — negative because
    #   suspicious volume erodes signal reliability, tilts risk-off
    "wash_trade_penalty":  {"1d": 0.30, "1w": 0.20, "1m": 0.10},
    # P27: circuit-breaker mean-revert (near +10% → bearish next day)
    "circuit_mean_revert": {"1d": 0.35, "1w": 0.05, "1m": 0.00},
    # P28: volume-price divergence — light-volume rally fades; light-volume
    #   drop bounces
    "vol_price_divergence":{"1d": 0.25, "1w": 0.20, "1m": 0.10},
    # P29: cross-symbol broker edge — one broker leading many names in a
    #   sector is a strong institutional signal
    "cross_symbol_broker": {"1d": 0.30, "1w": 0.45, "1m": 0.30},
    # P30: post-announcement drift — bullish news + flat price = drift-up
    "announcement_drift":  {"1d": 0.15, "1w": 0.40, "1m": 0.25},
    # P31: beta-adjusted return — today's alpha (residual over NEPSE)
    "beta_adjusted_alpha": {"1d": 0.35, "1w": 0.20, "1m": 0.10},
}


@dataclass
class SignalContribution:
    """One source's signed log-odds contribution to the final probability."""
    source: str
    strength: float           # raw signed magnitude (dimensionless, normalized to ~[-3, +3])
    coefficient: float        # per-horizon coefficient
    log_odds: float           # strength * coefficient (signed)
    note: str = ""

    @property
    def prob_shift(self) -> float:
        """How much this contribution moved probability_up in absolute terms."""
        return 1 / (1 + math.exp(-self.log_odds)) - 0.5


@dataclass
class EnsembleSignal:
    """Aggregated multi-source signal for one (symbol, horizon)."""
    horizon: Horizon
    probability_up: float
    direction: Literal["up", "down", "flat"]
    log_odds_total: float
    contributions: list[SignalContribution] = field(default_factory=list)

    def top_drivers(self, n: int = 5) -> list[SignalContribution]:
        """The n contributions with the largest absolute log-odds impact."""
        return sorted(self.contributions, key=lambda c: -abs(c.log_odds))[:n]

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon,
            "probability_up": round(self.probability_up, 4),
            "direction": self.direction,
            "log_odds_total": round(self.log_odds_total, 3),
            "contributions": [
                {"source": c.source, "strength": round(c.strength, 3),
                 "coefficient": c.coefficient, "log_odds": round(c.log_odds, 3),
                 "note": c.note}
                for c in self.contributions
            ],
            "top_drivers": [c.source for c in self.top_drivers(5)],
        }


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _add(contribs: list[SignalContribution], source: str, strength: float,
         horizon: Horizon, note: str = "") -> None:
    coef = COEFFICIENTS.get(source, {}).get(horizon, 0.0)
    if coef == 0.0:
        return
    contribs.append(SignalContribution(
        source=source, strength=strength, coefficient=coef,
        log_odds=strength * coef, note=note,
    ))


def extract_sources(state: dict, horizon: Horizon) -> list[SignalContribution]:
    """Walk the state produced by upstream nodes and turn each signal
    source into a `SignalContribution` with per-horizon weighting.
    """
    contribs: list[SignalContribution] = []

    snap = ((state.get("technicals_signal") or {}).get("indicator_snapshot") or {})
    kronos = state.get("kronos_forecast") or {}

    # ── Indicator composite ────────────────────────────────────────────
    net_bias = snap.get("net_bias_score") or 0.0
    if net_bias:
        _add(contribs, "indicator_net_bias", net_bias, horizon,
             f"composite bias {net_bias:+.2f}")

    # ── Kronos ─────────────────────────────────────────────────────────
    kd = kronos.get("direction_score")
    if kd is not None:
        _add(contribs, "kronos_direction", kd, horizon, f"kronos dir {kd:+.2f}")
    ks = kronos.get("short_horizon_pct")
    if ks is not None:
        _add(contribs, "kronos_short_pct", ks, horizon, f"kronos short {ks:+.2f}%")
    kw = kronos.get("swing_horizon_pct")
    if kw is not None:
        _add(contribs, "kronos_swing_pct", kw, horizon, f"kronos swing {kw:+.2f}%")

    # ── MTF confluence + timeframe biases ──────────────────────────────
    confluence = snap.get("confluence") or {}
    cscore = confluence.get("score") or 0.0
    if cscore:
        _add(contribs, "mtf_confluence", cscore * 3, horizon,
             f"3-TF alignment score {cscore:+.2f}")
    mtf = snap.get("multi_timeframe") or {}
    weekly_bias = (mtf.get("1w") or {}).get("net_bias_score") or 0.0
    if weekly_bias:
        _add(contribs, "weekly_bias", weekly_bias, horizon,
             f"weekly bias {weekly_bias:+.2f}")
    monthly_bias = (mtf.get("1m") or {}).get("net_bias_score") or 0.0
    if monthly_bias:
        _add(contribs, "monthly_bias", monthly_bias, horizon,
             f"monthly bias {monthly_bias:+.2f}")

    # ── Trend / Hurst regimes ──────────────────────────────────────────
    trend = snap.get("trend") or {}
    tregime = trend.get("regime")
    if tregime == "uptrend":
        _add(contribs, "trend_regime", 1.0, horizon, "uptrend regime")
    elif tregime == "downtrend":
        _add(contribs, "trend_regime", -1.0, horizon, "downtrend regime")
    hregime = trend.get("hurst_regime")
    if hregime == "momentum" and (net_bias != 0):
        # Momentum regime amplifies whichever direction the bias points
        _add(contribs, "hurst_regime", 0.5 * (1 if net_bias > 0 else -1),
             horizon, f"Hurst momentum ({trend.get('hurst_exponent')})")
    elif hregime == "mean_revert" and (net_bias != 0):
        # Mean-revert regime FADES momentum (opposite sign)
        _add(contribs, "hurst_regime", -0.5 * (1 if net_bias > 0 else -1),
             horizon, f"Hurst mean-revert ({trend.get('hurst_exponent')})")

    # ── Sector rotation ────────────────────────────────────────────────
    ticker = state.get("ticker")
    if ticker:
        try:
            from argos.data.nepse_sectors import symbol_to_sector, sector_relative_strength, today_sector_scores
            sector = symbol_to_sector(ticker)
            if sector:
                rs = sector_relative_strength(sector, window=20)
                if rs and rs.get("relative_strength_pct") is not None:
                    rs_val = rs["relative_strength_pct"]
                else:
                    try:
                        rs_val = today_sector_scores().get(sector, 0.0)
                    except Exception:
                        rs_val = 0.0
                if rs_val:
                    _add(contribs, "sector_rs", rs_val, horizon,
                         f"{sector} RS {rs_val:+.2f}%")
        except Exception:
            pass

    # ── Market breadth ─────────────────────────────────────────────────
    try:
        from argos.data.nepse_api import market_breadth
        b = market_breadth()
        ad = b.get("advance_decline_ratio")
        if ad is not None:
            # log(A/D) gives a symmetric signal around 1 (neutral)
            ad_signal = math.log(max(ad, 0.05))
            _add(contribs, "breadth_ad", ad_signal, horizon,
                 f"A/D ratio {ad:.2f}")
    except Exception:
        pass

    # ── Macro regime ───────────────────────────────────────────────────
    try:
        from argos.data.macro_context import current_regime
        regime, _reasons = current_regime()
        if regime == "risk_on":
            _add(contribs, "macro_regime", 1.0, horizon, "macro risk_on")
        elif regime == "risk_off":
            _add(contribs, "macro_regime", -1.0, horizon, "macro risk_off")
    except Exception:
        pass

    # ── LLM debate + judge ─────────────────────────────────────────────
    bull_arg = state.get("bull_argument") or {}
    bear_arg = state.get("bear_argument") or {}
    if bull_arg.get("confidence") is not None:
        _add(contribs, "bull_conf", (bull_arg["confidence"] - 0.5) * 2, horizon,
             f"bull conf {bull_arg['confidence']:.2f}")
    if bear_arg.get("confidence") is not None:
        _add(contribs, "bear_conf", -(bear_arg["confidence"] - 0.5) * 2, horizon,
             f"bear conf {bear_arg['confidence']:.2f}")
    judge = state.get("judge_verdict") or {}
    if judge.get("winner_side") in ("bull", "bear"):
        strength = (judge.get("confidence", 0.5) - 0.5) * 2  # 0..1 → -1..+1 scale from 0.5
        sign = 1 if judge["winner_side"] == "bull" else -1
        _add(contribs, "judge", strength * sign, horizon,
             f"judge {judge['winner_side']} @ {judge.get('confidence', 0):.2f}")

    # ── Fundamentals ───────────────────────────────────────────────────
    if ticker:
        try:
            from argos.fundamentals import compute_f_score, sector_rank
            f = compute_f_score(ticker)
            r = sector_rank(ticker)
            if f:
                # Center on max/2 → ±4.5 range
                _add(contribs, "f_score", f["raw_score"] - f["max_possible"] / 2, horizon,
                     f"F-score {f['raw_score']}/{f['max_possible']}")
            if r:
                # 0..100 → -50..+50, then normalized to ±3
                _add(contribs, "sector_rank", (r["composite_score"] - 50) / 15, horizon,
                     f"sector rank {r['composite_score']}")
        except Exception:
            pass

    # ── Candles ─────────────────────────────────────────────────────────
    candles = snap.get("candles") or {}
    br = candles.get("bull_ringer_score") or 0.0
    if br:
        _add(contribs, "bull_ringer", br, horizon, f"bull_ringer {br:.2f}")
    br2 = candles.get("bear_ringer_score") or 0.0
    if br2:
        _add(contribs, "bear_ringer", -br2, horizon, f"bear_ringer {br2:.2f}")

    # ── Smart-money (P9 floorsheet) ─────────────────────────────────────
    as_of_date = state.get("as_of_date", "")
    if ticker:
        try:
            from argos.data.floorsheet import smart_money_score
            sm = smart_money_score(ticker, as_of_date, lookback_days=10)
            if sm:
                _add(contribs, "smart_money", sm * 3, horizon,
                     f"floorsheet smart-money {sm:+.2f}")
        except Exception:
            pass

    # ── Broker reputation (P21) — WHO is doing the buying/selling? ─────
    if ticker:
        try:
            from argos.data.broker_reputation import smart_broker_context
            ctx = smart_broker_context(ticker, as_of_date)
            be = ctx.get("top_buyer_edge") or 0.0
            se = ctx.get("top_seller_edge") or 0.0
            if be:
                _add(contribs, "broker_edge_buyer", be * 2, horizon,
                     f"top buyer #{ctx.get('top_buyer_id')} edge {be:+.2f}")
            if se:
                # High edge_seller means when THIS broker sells, price falls —
                # so if they're selling today, it's a bearish signal (inverted)
                _add(contribs, "broker_edge_seller", -se * 2, horizon,
                     f"top seller #{ctx.get('top_seller_id')} edge {se:+.2f}")
        except Exception:
            pass

    # ── Flow analytics (P22-P28) ────────────────────────────────────────
    if ticker and as_of_date:
        ohlcv = ((state.get("ohlcv_window") or {}).get("ohlcv")) or []
        try:
            from argos.data.flow_analytics import (
                order_flow_imbalance, block_trade_score,
                closing_bias, distinct_buyer_ratio, turnover_surge,
                wash_trade_ratio, circuit_breaker_proximity,
                volume_price_divergence,
            )
            ofi = order_flow_imbalance(ticker, as_of_date)
            if ofi is not None and ofi != 0:
                _add(contribs, "order_flow_imbalance", ofi * 3, horizon,
                     f"top-3 buy vs sell NPR concentration {ofi:+.2f}")
            block = block_trade_score(ticker, as_of_date)
            if block and block.get("n_blocks", 0) > 0:
                bias = block.get("block_side_bias") or 0
                if bias:
                    _add(contribs, "block_trade_bias", bias * 2.5, horizon,
                         f"{block['n_blocks']} block trades, side_bias {bias:+.2f}")
            cb = closing_bias(ticker)
            if cb is not None and cb != 0:
                _add(contribs, "closing_bias", cb * 2, horizon,
                     f"closed at {int((cb+1)*50)}% of day's range")
            db = distinct_buyer_ratio(ticker, as_of_date)
            if db is not None:
                # Center at 0.5 (typical breadth); >0.5 = broader demand
                _add(contribs, "buyer_breadth", (db - 0.5) * 4, horizon,
                     f"distinct-buyer ratio {db:.2f}")
            ts_val = turnover_surge(ticker, as_of_date, ohlcv)
            if ts_val is not None and ts_val != 0:
                _add(contribs, "turnover_surge", ts_val, horizon,
                     f"turnover {ts_val:+.2f} log-ratio vs 20d avg")
            # P26: wash trade — treat as risk-off penalty
            wt = wash_trade_ratio(ticker, as_of_date)
            if wt is not None and wt >= 0.15:
                # Convert 0..1 fraction (0.15+) to signed -1..+1 penalty
                penalty_strength = -min(1.0, (wt - 0.15) / 0.35)
                _add(contribs, "wash_trade_penalty", penalty_strength * 2, horizon,
                     f"self-cross ratio {wt:.2%} — likely wash trading")
            # P27: circuit-breaker mean-revert
            cbp = circuit_breaker_proximity(ticker)
            if cbp is not None and cbp != 0:
                _add(contribs, "circuit_mean_revert", cbp * 2, horizon,
                     f"circuit proximity {cbp:+.2f}")
            # P28: volume-price divergence
            vpd = volume_price_divergence(ticker, ohlcv)
            if vpd is not None and vpd != 0:
                _add(contribs, "vol_price_divergence", vpd * 2, horizon,
                     f"vol-price divergence {vpd:+.2f}")
        except Exception:
            pass

    # ── Cross-symbol broker patterns (P29) ─────────────────────────────
    if ticker and as_of_date:
        try:
            from argos.data.broker_patterns import cross_symbol_broker_edge
            xs = cross_symbol_broker_edge(ticker, as_of_date, window_days=5)
            if xs:
                _add(contribs, "cross_symbol_broker", xs * 2, horizon,
                     f"cross-symbol broker edge {xs:+.2f}")
        except Exception:
            pass

    # ── Announcement-price gap (P30) ───────────────────────────────────
    if ticker and as_of_date:
        try:
            from argos.data.announcement_reaction import announcement_price_gap
            ohlcv = ((state.get("ohlcv_window") or {}).get("ohlcv")) or []
            gap = announcement_price_gap(ticker, as_of_date, ohlcv)
            if gap is not None and gap != 0:
                _add(contribs, "announcement_drift", gap * 2, horizon,
                     f"announcement-price gap {gap:+.2f}")
        except Exception:
            pass

    # ── Beta-adjusted alpha (P31) ──────────────────────────────────────
    if ticker:
        try:
            from argos.data.beta_adjusted import beta_adjusted_return_signal
            ohlcv = ((state.get("ohlcv_window") or {}).get("ohlcv")) or []
            baa = beta_adjusted_return_signal(ticker, ohlcv)
            if baa is not None and baa != 0:
                _add(contribs, "beta_adjusted_alpha", baa * 2, horizon,
                     f"today's alpha {baa:+.2f}σ vs beta prediction")
        except Exception:
            pass

    # ── Nepali sentiment (P10) ──────────────────────────────────────────
    if ticker:
        try:
            from argos.data.nepali_sentiment import sentiment_score
            ns = sentiment_score(ticker)
            if ns:
                _add(contribs, "nepali_sentiment", ns * 2, horizon,
                     f"Nepali news sentiment {ns:+.2f}")
        except Exception:
            pass

    # ── Divergences ─────────────────────────────────────────────────────
    d = snap.get("divergences") or {}
    d_strength = d.get("strength_score") or 0.0
    if d_strength:
        # Divergence bias sign: bull divergences vs bear
        bull_div = (d.get("regular_bullish_rsi", 0) + d.get("regular_bullish_macd", 0)
                    + d.get("regular_bullish_obv", 0))
        bear_div = (d.get("regular_bearish_rsi", 0) + d.get("regular_bearish_macd", 0)
                    + d.get("regular_bearish_obv", 0))
        sign = 1 if bull_div > bear_div else (-1 if bear_div > bull_div else 0)
        if sign:
            _add(contribs, "divergence", d_strength * sign, horizon,
                 f"divergence strength {d_strength:.2f}")

    return contribs


def combine(contributions: list[SignalContribution], horizon: Horizon) -> EnsembleSignal:
    """Sum log-odds contributions and produce final probability_up."""
    total = sum(c.log_odds for c in contributions)
    p_up = _clamp(_sigmoid(total), 0.05, 0.95)
    if p_up > 0.55:
        direction = "up"
    elif p_up < 0.45:
        direction = "down"
    else:
        direction = "flat"
    return EnsembleSignal(
        horizon=horizon, probability_up=round(p_up, 4),
        direction=direction, log_odds_total=round(total, 3),
        contributions=contributions,
    )


def combined_probability(state: dict, horizon: Horizon) -> EnsembleSignal:
    """Convenience: extract + combine in one call."""
    return combine(extract_sources(state, horizon), horizon)
