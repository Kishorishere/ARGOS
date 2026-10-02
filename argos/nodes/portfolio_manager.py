"""
Portfolio Manager node.

Final synthesis across all upstream outputs.
Cannot exceed the Risk Manager's max_position_size under any circumstance.
If vetoed, produces HOLD directly with the Risk Manager's reasoning —
but still captures the prompt for audit trail.
"""

from __future__ import annotations

import logging
from pathlib import Path

from argos.fundamentals import hard_reject
from argos.model_router import get_llm
from argos.portfolio.sizing import SizingInputs, compute_size
from argos.schemas import FinalDecision
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "portfolio_manager.md"

# Terminal defaults (`LONG_TERM_CONFIG` in nepse-quant-terminal). Applied
# to every fresh BUY that the LLM doesn't override with a specific value.
# These match the audited horizon: 40-day hold, -8% hard stop, -10% trail.
DEFAULT_HOLD_BARS = 40
DEFAULT_STOP_PCT = 0.08
DEFAULT_TRAIL_ATR = 3.0     # Chandelier-style trail: 3×ATR (~10% at 3% ATR)


def _load_prompt() -> str:
    return PROMPT_PATH.read_text()


def _format_prompt(state: FuseState) -> str:
    return _load_prompt().format(
        ticker=state["ticker"],
        as_of_date=state["as_of_date"],
    )


def _build_full_trail(state: FuseState) -> str:
    """Build a summary of all upstream outputs for the Portfolio Manager."""
    parts = []
    for key, name in [
        ("fundamentals_signal", "Fundamentals Analyst"),
        ("sentiment_signal", "Sentiment Analyst"),
        ("technicals_signal", "Technicals Analyst"),
        ("bull_argument", "Bull Advocate"),
        ("bear_argument", "Bear Advocate"),
        ("judge_verdict", "Blind Judge (independent verdict on debate)"),
        ("risk_verdict", "Risk Manager"),
    ]:
        data = state.get(key)
        if data:
            parts.append(f"### {name}\n{data}")
        else:
            parts.append(f"### {name}\n(No output)")
    return "\n\n".join(parts)


def _extract_atr_pct(state: FuseState) -> float | None:
    """Pull atr_pct from the technicals signal's indicator_snapshot so the LLM
    can size stops in real percentage terms."""
    ts = state.get("technicals_signal") or {}
    snap = ts.get("indicator_snapshot") or {}
    vol = snap.get("volatility") or {}
    return vol.get("atr_pct")


def _build_debate_summary(state: FuseState) -> str:
    """Format the debate transcript for the Portfolio Manager."""
    transcript = state.get("debate_transcript", [])
    if not transcript:
        return "(No debate transcript available)"
    parts = []
    for entry in transcript:
        side = entry.get("side", "?").upper()
        pos = entry.get("position", "?")
        conf = entry.get("confidence", 0)
        evidence = entry.get("strongest_evidence", "")
        reasoning = entry.get("reasoning", "")
        parts.append(
            f"[Round {entry.get('round', 1)} — {side}] "
            f"position={pos} confidence={conf:.2f}\n"
            f"  Strongest evidence: {evidence}\n"
            f"  Reasoning: {reasoning}"
        )
    return "\n\n".join(parts)


def portfolio_manager_node(state: FuseState) -> dict:
    """Run the Portfolio Manager agent — final synthesis."""
    agent_name = "portfolio_manager"
    ticker = state["ticker"]
    logger.info(
        "Running %s for %s as of %s",
        agent_name,
        ticker,
        state["as_of_date"],
    )

    # ── Bank fundamentals hard-reject (item 5) ──────────────────────
    # If the latest quarterly filing fails a regulatory / distress gate
    # (CAR < 8.5%, NPL > 6%, negative net profit), skip the LLM and emit
    # a forced HOLD. Applies to banks, dev-banks, finance cos, MFI.
    reject = hard_reject(ticker)
    if reject and reject.get("rejected"):
        why = "; ".join(reject.get("reasons") or [])
        logger.info("%s HARD-REJECT %s (%s): %s",
                    agent_name, ticker, reject.get("quarter", "?"), why)
        return {
            "final_decision": {
                "action": "hold",
                "confidence": 0.0,
                "position_size": 0.0,
                "rationale": f"Hard-reject: {why}",
                "dissent_notes": (f"Fundamentals hard-reject (sector "
                                  f"{reject.get('sector')}): {why}"),
            },
        }

    try:
        llm = get_llm(agent_name)
        structured_llm = llm.with_structured_output(FinalDecision)
        system_prompt = _format_prompt(state)

        full_trail = _build_full_trail(state)
        debate_summary = _build_debate_summary(state)
        risk_verdict = state.get("risk_verdict") or {}
        judge = state.get("judge_verdict") or {}
        atr_pct = _extract_atr_pct(state)

        # U3: give the LLM concrete stop-sizing guidance based on realized vol.
        # 1.5×atr_pct is a common Wilder-style stop; 2× that is a 2:1 R:R.
        atr_block = (
            f"## Volatility for stop-sizing\n"
            f"ATR%% (current): {atr_pct:.2f}% — use this to size your stops:\n"
            f"  - Suggested stop_loss_pct: {1.5*atr_pct/100:.4f} (≈ 1.5×ATR%%)\n"
            f"  - Suggested take_profit_pct: {3.0*atr_pct/100:.4f} (2:1 R:R vs stop)\n"
            if atr_pct is not None else
            "## Volatility for stop-sizing\nATR%% unavailable — default stop 3%, target 6%.\n"
        )

        judge_block = ""
        if judge:
            judge_block = (
                f"## Blind Judge Verdict (bias-corrected — weight this heavily)\n"
                f"Winner (blind): {judge.get('winner')}  ->  actual side: {judge.get('winner_side')}\n"
                f"Confidence: {judge.get('confidence', 0):.2f}\n"
                f"Key factor: {judge.get('key_factor', '')}\n"
                f"Reasoning: {judge.get('reasoning', '')}\n\n"
            )

        # P17: expose multi-horizon direction/profit projections to the LLM
        mhf = state.get("multi_horizon_forecast") or {}
        mhf_block = ""
        if mhf and mhf.get("horizons"):
            hz = mhf["horizons"]
            def _fmt(h):
                return (f"    {h['direction']:>4s}  p_up={h['probability_up']:.2f}  "
                        f"expected {h['expected_return_pct']:+.2f}%  "
                        f"band [p10={h['p10_return_pct']:+.2f}%, p90={h['p90_return_pct']:+.2f}%]  "
                        f"conf {h['confidence']:.2f}")
            mhf_block = (
                f"## Multi-Horizon Forecast (deterministic, no-LLM projections)\n"
                f"  1-day:  {_fmt(hz['1d'])}\n"
                f"  1-week: {_fmt(hz['1w'])}\n"
                f"  1-month:{_fmt(hz['1m'])}\n"
                f"Prefer setups where 1d AND 1w agree in direction. If they disagree,\n"
                f"trust 1w (more predictable) unless you can name a specific 1d catalyst.\n\n"
            )

        user_message = (
            f"Final decision for {state['ticker']} as of {state['as_of_date']}.\n\n"
            f"## Full Reasoning Trail\n{full_trail}\n\n"
            f"## Debate Transcript\n{debate_summary}\n\n"
            f"{mhf_block}"
            f"{judge_block}"
            f"{atr_block}\n"
            f"## Risk Manager Constraint\n"
            f"Max position size: {risk_verdict.get('max_position_size', 0.0)}\n"
            f"Veto: {risk_verdict.get('veto', False)}\n"
            f"Risk reasoning: {risk_verdict.get('reasoning', '')}\n\n"
            f"Synthesize everything into your final decision INCLUDING the exit plan "
            f"(stop_loss_pct, take_profit_pct, trailing_stop_atr, time_stop_bars). "
            f"On HOLD, set all exit fields to null."
        )

        result: FinalDecision = structured_llm.invoke([
            ("system", system_prompt),
            ("user", user_message),
        ])

        decision_dict = result.model_dump()

        # ── U2: override the LLM's guess with Kelly-fractional sizing ──
        # The LLM is bad at translating "conviction" into a fraction of
        # capital. We compute the size from evidence — Kelly-fractional on
        # edge/variance, then multiplied by an agent-agreement conviction
        # factor and capped by the risk manager's max.
        snap = (state.get("technicals_signal") or {}).get("indicator_snapshot") or {}
        kronos = state.get("kronos_forecast") or {}
        sizing_inputs = SizingInputs(
            action=decision_dict["action"],
            net_bias_score=float(snap.get("net_bias_score") or 0.0),
            kronos_direction=float(kronos.get("direction_score") or 0.0),
            kronos_short_pct=kronos.get("short_horizon_pct"),
            atr_pct=(snap.get("volatility") or {}).get("atr_pct"),
            bull_conf=float((state.get("bull_argument") or {}).get("confidence", 0.0)),
            bear_conf=float((state.get("bear_argument") or {}).get("confidence", 0.0)),
            judge_confidence=float((state.get("judge_verdict") or {}).get("confidence", 0.5)),
            judge_side=(state.get("judge_verdict") or {}).get("winner_side", "tie"),
            risk_max_position_size=float(risk_verdict.get("max_position_size", 0.0)),
        )
        kelly_out = compute_size(sizing_inputs)
        llm_size = decision_dict["position_size"]
        decision_dict["position_size"] = kelly_out["size"]
        decision_dict["sizing_trace"] = kelly_out
        decision_dict["sizing_llm_original"] = llm_size

        # ── Item 6: inject terminal-style hold/stop/trail defaults on BUY ──
        # Only fill fields the LLM left null. The LLM keeps authority to
        # override with a specific, reasoned value.
        if decision_dict["action"] == "buy" and decision_dict["position_size"] > 0:
            if decision_dict.get("stop_loss_pct") is None:
                decision_dict["stop_loss_pct"] = DEFAULT_STOP_PCT
            if decision_dict.get("trailing_stop_atr") is None:
                decision_dict["trailing_stop_atr"] = DEFAULT_TRAIL_ATR
            if decision_dict.get("time_stop_bars") is None:
                decision_dict["time_stop_bars"] = DEFAULT_HOLD_BARS
            if decision_dict.get("take_profit_pct") is None:
                # Terminal doesn't run a hard target; leaving None keeps the
                # trail as the only upside limit.
                pass

        logger.info(
            "%s decision: %s (conf %.2f)  |  size: LLM %.3f -> Kelly %.3f  (edge %+.2f%%, agreement %.2f)",
            agent_name, decision_dict["action"], decision_dict["confidence"],
            llm_size, kelly_out["size"], kelly_out.get("edge_pct", 0),
            kelly_out.get("agent_agreement", 0),
        )

        # Capture prompt for audit trail
        existing_prompts = dict(state.get("analyst_prompts") or {})

        return {
            "final_decision": decision_dict,
            "analyst_prompts": {
                **existing_prompts,
                "portfolio_manager": {"system": system_prompt, "user": user_message},
            },
        }

    except Exception as e:
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        # Emit a defensive HOLD rather than None so the journal always has a
        # decision and downstream code (backtest driver, simulator) doesn't
        # need to special-case failed runs.
        return {
            "final_decision": {
                "action": "hold",
                "confidence": 0.0,
                "position_size": 0.0,
                "rationale": f"Portfolio manager errored: {e}. "
                             f"Defaulting to HOLD.",
                "dissent_notes": "Upstream or LLM failure — no live decision.",
            },
            "errors": [error_msg],
        }


def veto_short_circuit(state: FuseState) -> dict:
    """Short-circuit path when Risk Manager vetoes — produces HOLD immediately."""
    logger.info("Veto short-circuit: producing HOLD decision without Portfolio Manager")
    risk_verdict = state.get("risk_verdict", {})
    return {
        "final_decision": {
            "action": "hold",
            "confidence": 0.0,
            "position_size": 0.0,
            "rationale": f"Trade vetoed by Risk Manager. {risk_verdict.get('reasoning', '')}",
            "dissent_notes": "Risk Manager exercised veto authority.",
        }
    }
