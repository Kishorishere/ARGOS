"""
LangGraph Graph Definition — Argos pipeline.

Topology (per §6 of build spec):
1. Three analysts run in PARALLEL: Fundamentals, Sentiment, Technicals
2. Debate: Bull argues first, Bear rebuts (sequential)
3. Risk Manager reviews the debate
4. CONDITIONAL: If vetoed → short-circuit to HOLD (skip Portfolio Manager)
                If approved → Portfolio Manager synthesizes final decision
5. END
"""

from __future__ import annotations

import logging
import os
import uuid

from langgraph.graph import END, START, StateGraph

from argos.state import FuseState
from argos.nodes.fundamentals import fundamentals_node
from argos.nodes.sentiment import sentiment_node
from argos.nodes.technicals import technicals_node
from argos.nodes.debate import bull_node, bear_node
from argos.nodes.exit_manager import exit_manager_node, should_short_circuit_exit
from argos.nodes.forecast import forecast_node
from argos.nodes.judge import judge_node
from argos.nodes.risk_manager import risk_manager_node, should_veto
from argos.nodes.portfolio_manager import portfolio_manager_node, veto_short_circuit
from argos.portfolio import load_portfolio

logger = logging.getLogger(__name__)


# ── Fan-out / Fan-in helpers ───────────────────────────────────────────────

ANALYST_NODES = ["fundamentals", "sentiment", "technicals"]


def _route_to_analysts(state: FuseState) -> list[str]:
    """Fan-out: return all analyst node names so LangGraph runs them in parallel."""
    return ANALYST_NODES


def _after_analysts(state: FuseState) -> str:
    """Fan-in: all analysts are done, move to the Bull Advocate."""
    return "bull"


def analyst_router(state: FuseState) -> dict:
    """Lightweight entry node that sets run_id before analysts fan out."""
    return {"run_id": state.get("run_id") or str(uuid.uuid4())}


def exit_short_circuit(state: FuseState) -> dict:
    """When the Exit Manager fires `exit_at_market`, produce a SELL final
    decision immediately (skipping analysts, debate, risk, portfolio LLM).
    """
    exit_d = state.get("exit_decision") or {}
    logger.info("Exit-manager short-circuit: producing SELL decision")
    return {
        "final_decision": {
            "action": "sell",
            "confidence": exit_d.get("confidence", 0.5),
            "position_size": 0.0,   # sell closes the position, sizing not applicable
            "rationale": f"Exit manager: {exit_d.get('reasoning', '')}",
            "dissent_notes": "Exit-manager short-circuit (thesis reversed).",
        },
    }


def should_debate_again(state: FuseState) -> str:
    """Conditional edge after bear_node.

    Increments happen inside bear_node itself, so here we just compare the
    current round against the configured max. If we've finished the last
    scheduled round, hand off to the Risk Manager; otherwise loop back to
    the Bull Advocate for the next round.
    """
    current_round = state.get("debate_round", 1)
    max_rounds = state.get("max_debate_rounds") or int(os.environ.get("MAX_DEBATE_ROUNDS", "2"))
    if current_round > max_rounds:
        return "done"
    return "bull_again"


def build_graph() -> StateGraph:
    """Build and return the Argos LangGraph graph.

    Graph topology:
        START → analyst_router
              → [fundamentals, sentiment, technicals] (parallel fan-out)
              → bull → bear (sequential debate)
              → risk_manager
              → [veto_short_circuit | portfolio_manager] (conditional)
              → END
    """
    graph = StateGraph(FuseState)

    # ── Add nodes ────────────────────────────────────────────────────────
    graph.add_node("exit_manager", exit_manager_node)         # U11: manage open position
    graph.add_node("exit_short_circuit", exit_short_circuit)  # U11: direct SELL path
    graph.add_node("analyst_router", analyst_router)

    # Analysts (fan-out targets — run in parallel)
    graph.add_node("fundamentals", fundamentals_node)
    graph.add_node("sentiment", sentiment_node)
    graph.add_node("technicals", technicals_node)

    # Multi-horizon forecast (P17) — runs after analysts, before debate
    graph.add_node("forecast", forecast_node)

    # Debate (sequential: Bull first, then Bear)
    graph.add_node("bull", bull_node)
    graph.add_node("bear", bear_node)

    # Blind judge (U1) — scores debate transcript with side labels hidden
    graph.add_node("judge", judge_node)

    # Risk & Decision
    graph.add_node("risk_manager", risk_manager_node)
    graph.add_node("veto_short_circuit", veto_short_circuit)
    graph.add_node("portfolio_manager", portfolio_manager_node)

    # ── Wire edges ───────────────────────────────────────────────────────
    # START → exit_manager (checks open positions FIRST — U11)
    graph.add_edge(START, "exit_manager")
    # From exit_manager: either exit-now shortcut, or continue to analysts
    graph.add_conditional_edges(
        "exit_manager", should_short_circuit_exit,
        {"exit_now": "exit_short_circuit", "continue": "analyst_router"},
    )
    graph.add_edge("exit_short_circuit", END)

    # Fan-out: router → all three analysts in parallel
    graph.add_conditional_edges("analyst_router", _route_to_analysts, ANALYST_NODES)

    # Fan-in: each analyst → forecast (LangGraph waits for all branches),
    # then forecast → bull
    for node in ANALYST_NODES:
        graph.add_conditional_edges(node, _after_analysts, {"bull": "forecast"})
    graph.add_edge("forecast", "bull")

    # Debate: Bull → Bear (sequential)
    graph.add_edge("bull", "bear")

    # After Bear: either loop back to Bull for the next round or run the
    # blind judge → then Risk Manager.
    graph.add_conditional_edges(
        "bear",
        should_debate_again,
        {
            "bull_again": "bull",
            "done": "judge",
        },
    )
    graph.add_edge("judge", "risk_manager")

    # Conditional edge from Risk Manager
    graph.add_conditional_edges(
        "risk_manager",
        should_veto,
        {
            "vetoed": "veto_short_circuit",
            "approved": "portfolio_manager",
        },
    )

    # Both paths lead to END
    graph.add_edge("veto_short_circuit", END)
    graph.add_edge("portfolio_manager", END)

    return graph


def compile_graph():
    """Compile the graph into an executable runnable."""
    graph = build_graph()
    compiled = graph.compile()
    logger.info("Argos graph compiled successfully")
    return compiled


def create_initial_state(
    ticker: str,
    as_of_date: str,
    ohlcv_window: dict | None = None,
) -> FuseState:
    """Create the initial state for a graph run."""
    return FuseState(
        ticker=ticker,
        as_of_date=as_of_date,
        ohlcv_window=ohlcv_window or {},
        fundamentals_signal=None,
        sentiment_signal=None,
        technicals_signal=None,
        kronos_forecast=None,
        bull_argument=None,
        bear_argument=None,
        debate_round=1,
        max_debate_rounds=int(os.environ.get("MAX_DEBATE_ROUNDS", "2")),
        debate_transcript=[],
        exit_decision=None,
        multi_horizon_forecast=None,
        judge_verdict=None,
        risk_verdict=None,
        final_decision=None,
        analyst_prompts={},
        portfolio_state=load_portfolio().to_risk_context(),
        run_id=str(uuid.uuid4()),
        errors=[],
    )
