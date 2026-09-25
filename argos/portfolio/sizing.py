"""Kelly-fractional position sizing (U2).

The 44-date backtest showed position sizes 0.03-0.12 uncorrelated with
signal quality — the weakest signal got the biggest size (0.12) while the
strongest got 0.05. This module computes a SIZE from evidence: expected
edge (basis points move Kronos + composite bias imply), realized volatility
(ATR%), and multi-agent agreement.

Formula (fractional Kelly, capped by risk manager):

    kelly_frac      = clip( edge_bps / (variance_bps + 1e-9), -1, 1 )
    conviction_mult = geo_mean(|net_bias|/5, |kronos_dir|, agent_agreement)
    raw_size        = FRACTION_OF_KELLY * kelly_frac * conviction_mult
    final_size      = clip( raw_size, 0, min(risk_max, HARD_CAP) )

`FRACTION_OF_KELLY` defaults to 0.25 (quarter-Kelly) — full Kelly is too
aggressive for a noisy free-tier LLM signal. Kelly's variance assumption
also breaks in fat-tailed markets; halving avoids blow-ups.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


FRACTION_OF_KELLY = 0.25   # quarter Kelly — the standard conservative default
HARD_CAP = 0.25            # never risk more than 25% of equity in one trade
MIN_ATR_PCT = 0.3          # sanity floor so we don't divide-by-tiny


@dataclass
class SizingInputs:
    """Everything the sizer needs, pulled from FuseState in the node."""
    action: str                # "buy" | "sell" | "hold"
    net_bias_score: float      # from indicator_composite
    kronos_direction: float    # from calibrated Kronos, in [-1, +1]
    kronos_short_pct: float | None
    atr_pct: float | None      # from IndicatorSnapshot.volatility
    bull_conf: float
    bear_conf: float
    judge_confidence: float    # 0.5 = coin flip; higher = judge more sure
    judge_side: str            # "bull" | "bear" | "tie"
    risk_max_position_size: float


def _geo_mean(*xs: float) -> float:
    """Geometric mean, robust to zero-input (returns 0 if any is 0)."""
    if any(x <= 0 for x in xs):
        return 0.0
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def _agent_agreement(bull: float, bear: float, judge_side: str, judge_conf: float,
                     action: str) -> float:
    """How aligned are our three internal voters (bull / bear / judge)?

    Returns 0..1. 1.0 = perfect alignment on action; 0.0 = maximally split.
    """
    # Convert everyone's vote into a signed opinion in [-1, +1]:
    #   +1 = strongly wants BUY, -1 = strongly wants SELL
    if action == "buy":
        bull_op = bull * 1
        bear_op = -bear
        judge_op = (judge_conf if judge_side == "bull"
                    else -judge_conf if judge_side == "bear" else 0)
    elif action == "sell":
        bull_op = -bull
        bear_op = bear * 1
        judge_op = (-judge_conf if judge_side == "bull"
                    else judge_conf if judge_side == "bear" else 0)
    else:
        return 0.0
    ops = [bull_op, bear_op, judge_op]
    # Positive-agreement fraction: how much of the total-magnitude points
    # the way we're actually trading?
    positive = sum(max(o, 0) for o in ops)
    magnitude = sum(abs(o) for o in ops) + 1e-9
    return positive / magnitude


def compute_size(inp: SizingInputs) -> dict[str, Any]:
    """Kelly-fractional size + full breakdown for the journal.

    Returns a dict with `size` (final position fraction) and every input
    that went into it, so the journal has a full accounting.
    """
    if inp.action == "hold":
        return {"size": 0.0, "reason": "hold action → no position"}

    atr = max(inp.atr_pct or 1.0, MIN_ATR_PCT)  # % (e.g., 2.5 for 2.5%)

    # ── Edge estimate (%) ──────────────────────────────────────────────
    # Expected next-day return in PERCENT (not bps). Kept small (0.5% max)
    # because we're mixing noisy composite bias with short-horizon Kronos.
    #   bias contribution:  ±5 net_bias → ±0.5%
    #   kronos direction:   ±1 → ±0.3%
    #   kronos short_pct:   already in %, shrunk 50% (magnitude noisy).
    bias_component = inp.net_bias_score * 0.1
    kronos_component = inp.kronos_direction * 0.3
    if inp.kronos_short_pct is not None:
        kronos_component += inp.kronos_short_pct * 0.5
    edge_pct = bias_component + kronos_component  # signed %

    # ── Variance estimate (%^2) ───────────────────────────────────────
    # ATR% squared is a reasonable proxy for one-day return variance.
    variance_pct2 = atr ** 2

    # ── Signed Kelly ─────────────────────────────────────────────────
    # Classic Kelly: f* = μ/σ² where both are in the same return-units.
    kelly = edge_pct / (variance_pct2 + 1e-9)
    if inp.action == "buy":
        kelly_signed = max(kelly, 0.0)
    else:  # sell
        kelly_signed = max(-kelly, 0.0)

    # ── Conviction multiplier ────────────────────────────────────────
    agreement = _agent_agreement(inp.bull_conf, inp.bear_conf,
                                 inp.judge_side, inp.judge_confidence, inp.action)
    bias_conv = min(abs(inp.net_bias_score) / 5.0, 1.0)
    kronos_conv = abs(inp.kronos_direction)
    conviction = _geo_mean(max(bias_conv, 0.05),
                            max(kronos_conv, 0.05),
                            max(agreement, 0.05))

    raw_size = FRACTION_OF_KELLY * kelly_signed * conviction

    # ── Phase E adjustments ────────────────────────────────────────────
    # P20: drawdown circuit-breaker — scale entire size by DD state
    try:
        from argos.risk.drawdown import drawdown_state
        dd = drawdown_state()
        dd_scale = dd.size_scale
        dd_status = dd.status
        dd_reason = dd.reason
    except Exception:
        dd_scale, dd_status, dd_reason = (1.0, "normal", "n/a")

    raw_size = raw_size * dd_scale

    final = max(0.0, min(raw_size, inp.risk_max_position_size, HARD_CAP))

    return {
        "size": round(final, 4),
        "edge_pct": round(edge_pct, 3),
        "variance_pct2": round(variance_pct2, 3),
        "kelly_signed": round(kelly_signed, 4),
        "conviction_mult": round(conviction, 4),
        "agent_agreement": round(agreement, 4),
        "atr_pct_used": round(atr, 3),
        "risk_cap": round(inp.risk_max_position_size, 4),
        "hard_cap": HARD_CAP,
        "fraction_of_kelly": FRACTION_OF_KELLY,
        # P20 audit
        "dd_scale": dd_scale,
        "dd_status": dd_status,
        "dd_reason": dd_reason,
    }
