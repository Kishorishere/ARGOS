"""
Debate node — Bull Advocate and Bear Advocate.

Single round by default: Bull argues from the three analyst outputs,
Bear responds directly to Bull's specific claims (not a generic bear case).

The debate runs sequentially: Bull first, then Bear rebuts.

Architecture:
- Both advocates receive the same analyst evidence (signals + numbers)
- Bull argues BUY if evidence supports it, Bear argues SELL if evidence supports it
- Neither is forced into a predetermined position — confidence reflects evidence strength
- After both speak, a structured debate_transcript is logged for audit
"""

from __future__ import annotations

import logging
from pathlib import Path

from argos.indicators import IndicatorSnapshot
from argos.model_router import get_llm
from argos.schemas import DebateArgument
from argos.state import FuseState

logger = logging.getLogger(__name__)

BULL_PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "bull_advocate.md"
BEAR_PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "bear_advocate.md"


def _load_prompt(path: Path) -> str:
    return path.read_text()


def _format_bull_prompt(state: FuseState) -> str:
    return _load_prompt(BULL_PROMPT_PATH).format(
        ticker=state["ticker"],
        as_of_date=state["as_of_date"],
    )


def _format_bear_prompt(state: FuseState) -> str:
    return _load_prompt(BEAR_PROMPT_PATH).format(
        ticker=state["ticker"],
        as_of_date=state["as_of_date"],
    )


def _render_technicals_signal(sig: dict) -> str:
    """Render the technicals signal with the LLM's take AND the raw snapshot.

    The advocates need both: the analyst's synthesized read (with a citable
    confidence) and the full IndicatorSnapshot so they can quote specific
    indicator values in their own arguments and rebuttals.
    """
    snap_dict = sig.get("indicator_snapshot") if isinstance(sig, dict) else None
    header = {k: v for k, v in sig.items() if k != "indicator_snapshot"} if isinstance(sig, dict) else sig
    parts = [f"Analyst take: {header}"]
    if snap_dict:
        try:
            snap = IndicatorSnapshot(**snap_dict)
            parts.append(snap.to_prompt_text())
        except Exception as e:
            parts.append(f"(IndicatorSnapshot rehydration failed: {e})")
    return "\n\n".join(parts)


def _build_analyst_summary(state: FuseState) -> str:
    """Format the three analyst outputs into a readable summary for the advocates."""
    parts = []
    for key, name in [
        ("fundamentals_signal", "Fundamentals Analyst"),
        ("sentiment_signal", "Sentiment Analyst"),
        ("technicals_signal", "Technicals Analyst"),
    ]:
        data = state.get(key)
        if not data:
            parts.append(f"### {name}\n(No output — agent may have failed)")
            continue
        if key == "technicals_signal":
            parts.append(f"### {name}\n{_render_technicals_signal(data)}")
        else:
            parts.append(f"### {name}\n{data}")
    return "\n\n".join(parts)


def _build_evidence_summary(state: FuseState) -> str:
    """Build a concise evidence block with Kronos forecast data for the advocates."""
    parts = []

    # Kronos forecast details (if available)
    kronos = state.get("kronos_forecast")
    if kronos:
        parts.append("### Kronos Price Forecast (Raw Data)")
        if "mean_forecast" in kronos:
            parts.append(f"- Mean forecast closes: {[f'{c:.2f}' for c in kronos['mean_forecast']]}")
        if "predicted_move_pct" in kronos:
            parts.append(f"- Predicted move: {kronos['predicted_move_pct']:+.2f}%")
        if "uncertainty_normalized" in kronos:
            avg_unc = sum(kronos["uncertainty_normalized"]) / len(kronos["uncertainty_normalized"])
            parts.append(f"- Average normalized uncertainty: {avg_unc:.4f}")
            if avg_unc < 0.005:
                parts.append("- Uncertainty: TIGHT (higher forecast confidence)")
            elif avg_unc < 0.015:
                parts.append("- Uncertainty: MODERATE")
            else:
                parts.append("- Uncertainty: WIDE (lower forecast confidence)")
        if "last_input_close" in kronos:
            parts.append(f"- Last input close: {kronos['last_input_close']:.2f}")
    else:
        parts.append("### Kronos Price Forecast\n(Not available)")

    return "\n\n".join(parts)


def _build_user_message(state: FuseState, analyst_summary: str, evidence_summary: str, role: str) -> str:
    """Build the user message for the Bull Advocate.

    On rounds > 1 the Bull is rebutting the previous round's Bear turn, so
    surface that specific argument in the prompt and ask for a targeted
    counter, not another opening statement.
    """
    ticker = state["ticker"]
    round_num = state.get("debate_round", 1)
    transcript = state.get("debate_transcript") or []
    prior_bear = None
    if round_num > 1:
        for entry in reversed(transcript):
            if entry.get("side") == "bear" and entry.get("round", 0) == round_num - 1:
                prior_bear = entry
                break

    lines = [
        f"Round {round_num} of the debate for {ticker} as of {state['as_of_date']}.",
        "",
        analyst_summary,
        "",
        evidence_summary,
    ]
    if prior_bear:
        lines += [
            "",
            f"## Bear Advocate's response in round {round_num - 1}",
            f"Position: {prior_bear.get('position')}   "
            f"Confidence: {prior_bear.get('confidence', 0):.2f}",
            f"Strongest evidence: {prior_bear.get('strongest_evidence','')}",
            f"Reasoning: {prior_bear.get('reasoning','')}",
            "",
            f"Rebut the Bear's specific points and either restate or update the bull case. "
            f"Do NOT reopen the debate from scratch — target the Bear's actual claims.",
        ]
    else:
        lines += [
            "",
            f"Based on this evidence, build the strongest case for {role}ing {ticker}. "
            f"If the evidence does not support this position, say so with low confidence.",
        ]
    return "\n".join(lines)


def _build_bear_user_message(
    state: FuseState,
    analyst_summary: str,
    evidence_summary: str,
    bull_argument: dict,
) -> str:
    """Build the user message for the Bear Advocate, including Bull's argument."""
    ticker = state["ticker"]
    return (
        f"The Bull Advocate argued for BUYING {ticker}.\n\n"
        f"## Bull Advocate's Argument\n{bull_argument}\n\n"
        f"## Analyst Evidence\n{analyst_summary}\n\n"
        f"## Kronos Forecast Data\n{evidence_summary}\n\n"
        f"Rebut the Bull's specific claims and build the bear case for SELLING. "
        f"If the Bull's evidence is strong, acknowledge it and explain why the bear case still has merit."
    )


def bull_node(state: FuseState) -> dict:
    """Run the Bull Advocate agent."""
    agent_name = "bull"
    logger.info(
        "Running %s for %s as of %s",
        agent_name,
        state["ticker"],
        state["as_of_date"],
    )

    try:
        llm = get_llm(agent_name)
        structured_llm = llm.with_structured_output(DebateArgument)
        system_prompt = _format_bull_prompt(state)

        analyst_summary = _build_analyst_summary(state)
        evidence_summary = _build_evidence_summary(state)
        user_message = _build_user_message(state, analyst_summary, evidence_summary, "buy")

        result: DebateArgument = structured_llm.invoke([
            ("system", system_prompt),
            ("user", user_message),
        ])

        bull_dict = result.model_dump()
        logger.info(
            "%s position: %s (confidence: %.2f)",
            agent_name,
            bull_dict["position"],
            bull_dict["confidence"],
        )

        # Build debate transcript entry
        transcript_entry = {
            "round": state.get("debate_round", 1),
            "side": "bull",
            "position": bull_dict["position"],
            "confidence": bull_dict["confidence"],
            "strongest_evidence": bull_dict["strongest_evidence"],
            "rebuttal_of_opposing_view": bull_dict["rebuttal_of_opposing_view"],
            "reasoning": bull_dict["reasoning"],
            "evidence_summary": bull_dict.get("evidence_summary", ""),
        }

        # Capture the prompt for audit trail
        prompt_record = {
            "system": system_prompt,
            "user": user_message,
        }

        existing_transcript = list(state.get("debate_transcript") or [])
        existing_prompts = dict(state.get("analyst_prompts") or {})

        return {
            "bull_argument": bull_dict,
            "debate_transcript": existing_transcript + [transcript_entry],
            "analyst_prompts": {**existing_prompts, "bull": prompt_record},
        }

    except Exception as e:
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {
            "bull_argument": None,
            "errors": [error_msg],
        }


def bear_node(state: FuseState) -> dict:
    """Run the Bear Advocate agent — rebuts Bull's specific claims."""
    agent_name = "bear"
    logger.info(
        "Running %s for %s as of %s",
        agent_name,
        state["ticker"],
        state["as_of_date"],
    )

    bull_argument = state.get("bull_argument")
    if not bull_argument:
        error_msg = f"{agent_name} node skipped — no bull_argument in state"
        logger.warning(error_msg)
        return {
            "bear_argument": None,
            "errors": [error_msg],
        }

    try:
        llm = get_llm(agent_name)
        structured_llm = llm.with_structured_output(DebateArgument)
        system_prompt = _format_bear_prompt(state)

        analyst_summary = _build_analyst_summary(state)
        evidence_summary = _build_evidence_summary(state)
        user_message = _build_bear_user_message(state, analyst_summary, evidence_summary, bull_argument)

        result: DebateArgument = structured_llm.invoke([
            ("system", system_prompt),
            ("user", user_message),
        ])

        bear_dict = result.model_dump()
        logger.info(
            "%s position: %s (confidence: %.2f)",
            agent_name,
            bear_dict["position"],
            bear_dict["confidence"],
        )

        # Build debate transcript entry
        transcript_entry = {
            "round": state.get("debate_round", 1),
            "side": "bear",
            "position": bear_dict["position"],
            "confidence": bear_dict["confidence"],
            "strongest_evidence": bear_dict["strongest_evidence"],
            "rebuttal_of_opposing_view": bear_dict["rebuttal_of_opposing_view"],
            "reasoning": bear_dict["reasoning"],
            "evidence_summary": bear_dict.get("evidence_summary", ""),
        }

        # Capture the prompt for audit trail
        prompt_record = {
            "system": system_prompt,
            "user": user_message,
        }

        existing_transcript = list(state.get("debate_transcript") or [])
        existing_prompts = dict(state.get("analyst_prompts") or {})
        current_round = state.get("debate_round", 1)

        return {
            "bear_argument": bear_dict,
            "debate_transcript": existing_transcript + [transcript_entry],
            "analyst_prompts": {**existing_prompts, "bear": prompt_record},
            # Advance the round counter — the graph's should_debate_again edge
            # will decide whether to loop back to bull or hand off to risk.
            "debate_round": current_round + 1,
        }

    except Exception as e:
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {
            "bear_argument": None,
            "errors": [error_msg],
            # Still advance the round so a downstream failure doesn't infinite-loop
            "debate_round": state.get("debate_round", 1) + 1,
        }
