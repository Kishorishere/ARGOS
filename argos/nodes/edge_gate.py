"""Edge gate node — selective reasoning layer.

Runs ONCE per (ticker, date) after the three analysts + the multi-horizon
forecast, BEFORE the bull-vs-bear debate. Reads what the analysts produced
and decides whether the expensive debate / judge / risk / PM stack is worth
running today, or whether the system should skip straight to HOLD.

Design:
- Cheap model tier (flash-lite). One structured call, ~1–2k tokens.
- Recall-biased: missing a trade is costly, running a debate that ends in
  HOLD is cheap. The prompt errs toward `proceed=True`.
- Env-gated via EDGE_GATE_ENABLED so A/B measurement is trivial. When
  disabled the node returns `proceed=True` without calling the LLM.
- On ANY error the node fails open (proceed=True) so a buggy gate can
  never silently suppress real trades.

Journal / audit: the full gate verdict (proceed, confidence, triggers,
reasoning) is written into state['gate_verdict'] and read by the journal
record builder. The paper's cost-vs-accuracy table is computed from this.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from argos.model_router import get_llm
from argos.schemas import EdgeGateVerdict
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "edge_gate.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text()


def _is_enabled() -> bool:
    """Env toggle so A/B (gate_on vs gate_off) is one env flip."""
    val = (os.environ.get("EDGE_GATE_ENABLED") or "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _summarize_analyst(sig: dict | None) -> str:
    if not sig:
        return "missing"
    factors = sig.get("key_factors") or []
    return (
        f"{sig.get('signal', '?')} (conf {float(sig.get('confidence') or 0):.2f})"
        f"  factors: {', '.join(str(f) for f in factors[:3])}"
    )


def _summarize_indicators(technicals_signal: dict | None) -> str:
    snap = (technicals_signal or {}).get("indicator_snapshot") or {}
    if not snap:
        return "no indicator snapshot"
    trend = (snap.get("trend") or {}).get("regime") or "?"
    vol = (snap.get("volatility") or {}).get("vol_regime") or "?"
    bull_ringer = (snap.get("candles") or {}).get("bull_ringer_score") or 0
    bear_ringer = (snap.get("candles") or {}).get("bear_ringer_score") or 0
    return (
        f"composite_bull={snap.get('composite_bull_score')}, "
        f"composite_bear={snap.get('composite_bear_score')}, "
        f"net_bias={snap.get('net_bias_score')}, "
        f"trend_regime={trend}, vol_regime={vol}, "
        f"bull_ringer={bull_ringer}, bear_ringer={bear_ringer}"
    )


def _summarize_forecast(forecast: dict | None) -> str:
    if not forecast:
        return "no forecast"
    horizons = forecast.get("horizons") or {}
    out = []
    for h_key in ("1d", "1w", "1m"):
        h = horizons.get(h_key) or {}
        if not h:
            continue
        out.append(
            f"{h_key}: dir={h.get('direction')} p_up={h.get('probability_up')} "
            f"conf={h.get('confidence')}"
        )
    return " | ".join(out) if out else "no horizons"


def _summarize_ohlcv(state: FuseState) -> str:
    ohlcv_window = state.get("ohlcv_window") or {}
    bars = ohlcv_window.get("ohlcv") or []
    if not bars:
        return "no ohlcv"
    last = bars[-1]
    closes = [float(b.get("close", 0) or 0) for b in bars[-20:]]
    hi_20 = max(closes) if closes else 0
    lo_20 = min(closes) if closes else 0
    last_close = float(last.get("close", 0) or 0)
    pos_in_range = (
        (last_close - lo_20) / (hi_20 - lo_20) if hi_20 > lo_20 else 0.5
    )
    return (
        f"last_close={last_close:.2f}, 20d_high={hi_20:.2f}, "
        f"20d_low={lo_20:.2f}, pos_in_range={pos_in_range:.2f}"
    )


def _default_proceed(reason: str) -> dict:
    """Fail-open default — we never want the gate to silently suppress
    real trading days. Any error / disabled state returns proceed=True with
    a reason so the audit trail is intact.
    """
    return {
        "proceed": True,
        "confidence": 0.0,
        "triggers": [],
        "reasoning": reason,
    }


def edge_gate_node(state: FuseState) -> dict:
    """Decide whether to run the full debate on this (ticker, date)."""
    agent_name = "edge_gate"
    ticker = state.get("ticker", "?")
    as_of_date = state.get("as_of_date", "?")

    if not _is_enabled():
        logger.debug("%s disabled via EDGE_GATE_ENABLED — proceeding", agent_name)
        return {"gate_verdict": _default_proceed("gate disabled (EDGE_GATE_ENABLED off)")}

    fundamentals = state.get("fundamentals_signal")
    sentiment = state.get("sentiment_signal")
    technicals = state.get("technicals_signal")
    forecast = state.get("multi_horizon_forecast")

    system_prompt = _load_prompt().format(ticker=ticker, as_of_date=as_of_date)
    user_message = (
        f"### Analyst signals\n"
        f"  fundamentals: {_summarize_analyst(fundamentals)}\n"
        f"  sentiment:    {_summarize_analyst(sentiment)}\n"
        f"  technicals:   {_summarize_analyst(technicals)}\n\n"
        f"### Indicators\n  {_summarize_indicators(technicals)}\n\n"
        f"### Multi-horizon forecast\n  {_summarize_forecast(forecast)}\n\n"
        f"### Price context\n  {_summarize_ohlcv(state)}\n\n"
        f"Return your gate verdict as JSON."
    )

    try:
        llm = get_llm(agent_name)
        structured = llm.with_structured_output(EdgeGateVerdict)
        result: EdgeGateVerdict = structured.invoke([
            ("system", system_prompt), ("user", user_message),
        ])
        verdict = result.model_dump()
        logger.info(
            "%s %s @ %s: proceed=%s conf=%.2f triggers=%s",
            agent_name, ticker, as_of_date,
            verdict["proceed"], verdict["confidence"],
            verdict.get("triggers") or [],
        )
        existing = dict(state.get("analyst_prompts") or {})
        return {
            "gate_verdict": verdict,
            "analyst_prompts": {
                **existing,
                "edge_gate": {"system": system_prompt, "user": user_message},
            },
        }
    except Exception as e:  # noqa: BLE001
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {
            "gate_verdict": _default_proceed(f"gate errored: {e}"),
            "errors": [error_msg],
        }


def should_skip_debate(state: FuseState) -> str:
    """Conditional edge after edge_gate_node.

    Routes the graph: "skip" short-circuits to a HOLD emitter and ends the
    run; "proceed" runs the normal bull/bear/judge/risk/PM chain.
    """
    verdict = state.get("gate_verdict") or {}
    if verdict.get("proceed") is False:
        return "skip"
    return "proceed"


def gate_hold_short_circuit(state: FuseState) -> dict:
    """Emit a HOLD FinalDecision when the gate said skip.

    Keeps the schema identical to what portfolio_manager would emit, so
    downstream evaluators (journal, simulator, eval_nasdaq) don't need a
    special case — a gated-out day looks exactly like any other HOLD day,
    with the rationale naming the gate.
    """
    verdict = state.get("gate_verdict") or {}
    reason = verdict.get("reasoning") or "edge gate skipped debate (no edge detected)"
    logger.info("Edge-gate short-circuit: HOLD for %s @ %s — %s",
                state.get("ticker"), state.get("as_of_date"), reason[:80])
    return {
        "final_decision": {
            "action": "hold",
            "confidence": float(verdict.get("confidence") or 0.0),
            "position_size": 0.0,
            "rationale": f"Edge gate skipped debate: {reason}",
            "dissent_notes": "Pre-debate gate short-circuit — bull/bear/judge/"
                             "risk/PM were not invoked on this bar.",
        },
    }
