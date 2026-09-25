"""
Sentiment Analyst node.

Reads news/sentiment data for the ticker, calls the LLM with Google Search
grounding + structured output, and writes the signal to state.

The LLM searches the web in real time for actual news, analyst ratings,
and social media sentiment — no stub, no hardcoded values.
"""

from __future__ import annotations

import logging
from pathlib import Path

from argos.model_router import get_llm
from argos.schemas import AnalystSignal
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "sentiment_analyst.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text()


def _format_prompt(state: FuseState) -> str:
    return _load_prompt().format(
        ticker=state["ticker"],
        as_of_date=state["as_of_date"],
    )


def sentiment_node(state: FuseState) -> dict:
    """Run the Sentiment Analyst agent with Google Search grounding."""
    agent_name = "sentiment"
    ticker = state["ticker"]
    as_of_date = state["as_of_date"]
    logger.info("Running %s for %s as of %s", agent_name, ticker, as_of_date)

    try:
        # get_llm returns a grounded LLM (with google_search tool bound)
        # for sentiment — configured in model_router.py
        llm = get_llm(agent_name)
        structured_llm = llm.with_structured_output(AnalystSignal)
        system_prompt = _format_prompt(state)

        # The user message gives the LLM specific search targets
        user_message = (
            f"Search for recent news, analyst ratings, and social media "
            f"sentiment about {ticker}.\n"
            f"Look for headlines from the last 1–2 weeks, any upgrades/downgrades, "
            f"and what retail investors on Reddit/X are saying.\n"
            f"Evaluate sentiment as of {as_of_date} and produce a "
            f"bullish/bearish/neutral signal with your confidence level."
        )

        result: AnalystSignal = structured_llm.invoke([
            ("system", system_prompt),
            ("user", user_message),
        ])

        signal_dict = result.model_dump()
        logger.info(
            "%s signal: %s (confidence: %.2f)",
            agent_name,
            signal_dict["signal"],
            signal_dict["confidence"],
        )

        # Capture prompt for audit trail
        existing_prompts = dict(state.get("analyst_prompts") or {})

        return {
            "sentiment_signal": signal_dict,
            "analyst_prompts": {
                **existing_prompts,
                "sentiment": {"system": system_prompt, "user": user_message},
            },
        }

    except Exception as e:
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {
            "sentiment_signal": None,
            "errors": [error_msg],
        }
