"""
Graph State — FuseState TypedDict schema.

Every node reads from and writes to this single shared state object.
No agent calls another agent directly — all communication flows through state.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def _merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Reducer for state keys that multiple parallel nodes may write to.

    LangGraph refuses concurrent writes to a plain (LastValue) channel; using
    this as an Annotated reducer lets each fan-out branch contribute its own
    slice (e.g. analyst_prompts['fundamentals']) and the graph merges them.
    """
    out = dict(left or {})
    out.update(right or {})
    return out


class FuseState(TypedDict, total=False):
    """Shared state that flows through the LangGraph graph."""

    # --- Run metadata ---
    ticker: str
    as_of_date: str
    run_id: str

    # --- Raw data ---
    ohlcv_window: dict[str, Any]  # raw price/volume data for this cycle

    # --- Analyst outputs ---
    fundamentals_signal: dict[str, Any] | None
    sentiment_signal: dict[str, Any] | None
    technicals_signal: dict[str, Any] | None

    # --- Kronos integration ---
    kronos_forecast: dict[str, Any] | None

    # --- Debate ---
    bull_argument: dict[str, Any] | None
    bear_argument: dict[str, Any] | None
    debate_round: int          # 1-indexed; increments after every bear turn
    max_debate_rounds: int     # hard cap; env MAX_DEBATE_ROUNDS
    debate_transcript: list[dict[str, Any]]  # structured log of each debate turn

    # --- Exit manager (U11) — runs first when an open position exists ---
    exit_decision: dict[str, Any] | None

    # --- Multi-horizon forecast (P17) — 1d/1w/1m direction+profit projections ---
    multi_horizon_forecast: dict[str, Any] | None

    # --- Blind judge (U1) ---
    judge_verdict: dict[str, Any] | None

    # --- Risk & Decision ---
    risk_verdict: dict[str, Any] | None
    final_decision: dict[str, Any] | None

    # --- Audit trail: exact prompts sent to each agent ---
    # Annotated so parallel analyst nodes can each contribute their entry
    # without triggering LangGraph's concurrent-write guard.
    analyst_prompts: Annotated[dict[str, dict[str, str]], _merge_dicts]

    # --- Portfolio state for Risk Manager context ---
    portfolio_state: dict[str, Any]  # positions, sector exposure, recent P&L

    # --- Error collection (non-fatal) ---
    # Annotated with operator.add so parallel nodes can append concurrently
    errors: Annotated[list[str], operator.add]
