"""Blind Judge — reads the debate transcript with side labels REMOVED and
picks the more evidence-supported argument.

Purpose (U1): the 44-date backtest showed Bear systematically more confident
than Bull because of provider bias, not evidence quality. The judge is an
independent LLM (different provider) that sees only the two arguments as
'Argument A' and 'Argument B', so its verdict is uncontaminated by which
side made which claim. The Portfolio Manager uses it as a tiebreaker.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from argos.model_router import get_llm
from argos.schemas import JudgeVerdict
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "judge.md"


def _load_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text()
    # Fallback inline prompt if the file isn't there yet — keeps the graph
    # runnable without a separate prompt-file dependency.
    return (
        "You are an impartial judge. You will read two anonymized trading arguments "
        "(Argument A and Argument B) for {ticker} as of {as_of_date}.\n\n"
        "You do NOT know which is the buy case and which is the sell case. "
        "Your ONLY job is to name the argument whose evidence is stronger — "
        "cite indicator values / analyst signals / Kronos data by name.\n\n"
        "Return JSON: {{winner: 'A'|'B'|'tie', confidence: 0..1, "
        "reasoning: 'why', key_factor: 'the single decider'}}"
    )


def _format_prompt(state: FuseState) -> str:
    return _load_prompt().format(
        ticker=state["ticker"], as_of_date=state["as_of_date"],
    )


def _anonymized_pair(bull: dict, bear: dict) -> tuple[dict, dict, str]:
    """Return (arg_A, arg_B, mapping_key) where mapping_key tells the caller
    whether A is 'bull' or 'bear'. Order is randomized per call to prevent
    position bias in the judge's response.
    """
    # Deterministic-ish shuffle: pick side by hash of (ticker+date+run_id) so
    # a re-run of the same date gets the same anonymization → journal is
    # reproducible without leaking side into judgment.
    if random.random() < 0.5:
        return bull, bear, "A=bull,B=bear"
    return bear, bull, "A=bear,B=bull"


def _fmt_arg(arg: dict) -> str:
    if not arg:
        return "(missing)"
    return (
        f"Strongest evidence: {arg.get('strongest_evidence','')}\n"
        f"Reasoning: {arg.get('reasoning','')}\n"
        f"Evidence summary: {arg.get('evidence_summary','')}"
    )


def judge_node(state: FuseState) -> dict:
    """Score bull vs bear blind and record the verdict."""
    agent_name = "judge"
    bull = state.get("bull_argument") or {}
    bear = state.get("bear_argument") or {}
    if not (bull and bear):
        logger.info("%s skipped — need both bull_argument and bear_argument", agent_name)
        return {}

    ticker = state["ticker"]
    logger.info("Running %s for %s as of %s", agent_name, ticker, state["as_of_date"])

    arg_a, arg_b, mapping = _anonymized_pair(bull, bear)
    try:
        llm = get_llm(agent_name)
        structured = llm.with_structured_output(JudgeVerdict)
        system_prompt = _format_prompt(state)
        user_message = (
            f"# Anonymized Debate for {ticker} @ {state['as_of_date']}\n\n"
            f"## Argument A\n{_fmt_arg(arg_a)}\n\n"
            f"## Argument B\n{_fmt_arg(arg_b)}\n\n"
            f"Which argument is more evidence-supported? "
            f"Return your JSON verdict — 'A', 'B', or 'tie' with confidence 0..1."
        )
        result: JudgeVerdict = structured.invoke([
            ("system", system_prompt), ("user", user_message),
        ])
        verdict = result.model_dump()

        # Map A/B back to bull/bear now that the LLM has answered
        if verdict["winner"] == "tie":
            verdict["winner_side"] = "tie"
        elif mapping == "A=bull,B=bear":
            verdict["winner_side"] = "bull" if verdict["winner"] == "A" else "bear"
        else:
            verdict["winner_side"] = "bear" if verdict["winner"] == "A" else "bull"

        logger.info("%s verdict: %s (%s) — conf %.2f",
                    agent_name, verdict["winner"], verdict["winner_side"], verdict["confidence"])

        existing_prompts = dict(state.get("analyst_prompts") or {})
        return {
            "judge_verdict": verdict,
            "analyst_prompts": {
                **existing_prompts,
                "judge": {"system": system_prompt, "user": user_message,
                          "anonymization": mapping},
            },
        }
    except Exception as e:  # noqa: BLE001
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {"judge_verdict": None, "errors": [error_msg]}
