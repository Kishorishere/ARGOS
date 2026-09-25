"""
Fundamentals Analyst node.

Reads fundamentals data (earnings, ratios, guidance) for the ticker,
calls the LLM with Google Search grounding + structured output,
and writes the signal to state.

The LLM searches the web in real time for actual financial data —
no stub, no hardcoded values.
"""

from __future__ import annotations

import logging
from pathlib import Path

from argos.fundamentals import compute_f_score, latest_quarter, sector_rank
from argos.model_router import get_llm
from argos.schemas import AnalystSignal
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "fundamentals_analyst.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text()


def _format_prompt(state: FuseState) -> str:
    return _load_prompt().format(
        ticker=state["ticker"],
        as_of_date=state["as_of_date"],
    )


def fundamentals_node(state: FuseState) -> dict:
    """Run the Fundamentals Analyst agent with Google Search grounding."""
    agent_name = "fundamentals"
    ticker = state["ticker"]
    as_of_date = state["as_of_date"]
    logger.info("Running %s for %s as of %s", agent_name, ticker, as_of_date)

    # ── P8: pull structured fundamentals if we have them on file ──────
    # When present, the LLM works from real numbers not grounded search.
    # F-score and sector rank feed the reasoning directly.
    latest_report = latest_quarter(ticker)
    f_score = compute_f_score(ticker)
    rank = sector_rank(ticker)
    fundamentals_block = ""
    if latest_report:
        fundamentals_block = (
            f"\n\n## STRUCTURED FUNDAMENTALS (from local store, not web search)\n"
            f"Latest report: {latest_report.quarter} (sector: {latest_report.sector})\n"
            f"  EPS: NPR {latest_report.eps_npr}   ROE: {latest_report.roe_pct}%   "
            f"ROA: {latest_report.roa_pct}%\n"
            f"  P/E: {latest_report.p_e_ratio}   P/B: {latest_report.p_b_ratio}\n"
            f"  NPL: {latest_report.npl_ratio_pct}%   CAR: {latest_report.cap_adequacy_pct}%\n"
        )
        if f_score:
            fundamentals_block += (
                f"  **Piotroski-like F-score: {f_score['raw_score']}/{f_score['max_possible']}** "
                f"→ **{f_score['grade'].upper()}**\n"
            )
        if rank:
            fundamentals_block += (
                f"  **Sector rank composite: {rank['composite_score']}/100 "
                f"({rank['grade']}) vs {rank['peers']} peers**\n"
                f"    ROE%ile {rank['roe_percentile']} / EPS%ile {rank['eps_percentile']} / "
                f"growth%ile {rank['profit_growth_percentile']} / NPL%ile {rank['npl_percentile']}\n"
            )
        fundamentals_block += (
            "\nWeigh this structured data HEAVILY — it's verified, not scraped. "
            "Only search the web for things not covered here (news, guidance, corporate actions).\n"
        )

    try:
        # get_llm returns a grounded LLM (with google_search tool bound)
        # for fundamentals — configured in model_router.py
        llm = get_llm(agent_name)
        structured_llm = llm.with_structured_output(AnalystSignal)
        system_prompt = _format_prompt(state)

        # The user message gives the LLM specific search targets
        user_message = (
            f"Search for {ticker}'s most recent quarterly earnings, "
            f"key financial ratios (P/E, revenue growth, debt-to-equity), "
            f"analyst ratings, and forward guidance.\n"
            f"Evaluate fundamentals as of {as_of_date} and produce a "
            f"bullish/bearish/neutral signal with your confidence level."
            f"{fundamentals_block}"
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
            "fundamentals_signal": signal_dict,
            "analyst_prompts": {
                **existing_prompts,
                "fundamentals": {"system": system_prompt, "user": user_message},
            },
        }

    except Exception as e:
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {
            "fundamentals_signal": None,
            "errors": [error_msg],
        }
