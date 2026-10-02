"""Exit-manager node (U11) — runs BEFORE the analyst fan-out when a position
exists for the ticker. Decides whether to keep the current stops running,
exit at market, tighten the stop, or move to breakeven.

Design: runs as a first-node in the graph and short-circuits directly to
END on `exit_at_market` (skipping the whole debate — we're closing, not
opening a new bet). Otherwise it emits a `pending_exit_action` in state
that the simulator honors, and continues to the analyst fan-out so we can
still evaluate whether ADDING to the position makes sense.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from argos.data.broker_signals import should_pump_exit
from argos.indicators import IndicatorSnapshot, compute_snapshot
from argos.model_router import get_llm
from argos.schemas import ExitDecision
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "exit_manager.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text()


def _find_open_position(state: FuseState) -> dict | None:
    """Return the open position dict for this ticker, or None."""
    portfolio = state.get("portfolio_state") or {}
    ticker = state.get("ticker", "").upper()
    for p in portfolio.get("positions") or []:
        if p.get("ticker", "").upper() == ticker:
            # Only manage LONG positions for now (matches simulator)
            if p.get("quantity", 0) > 0:
                return p
    return None


def exit_manager_node(state: FuseState) -> dict:
    """Decide what to do with an existing open position on `ticker`.

    Emits nothing (empty dict) when no position exists — the graph
    routes on the presence of state['exit_decision'] to short-circuit
    or continue to analysts.
    """
    agent_name = "exit_manager"
    position = _find_open_position(state)
    if not position:
        logger.debug("%s: no open position for %s — skipping", agent_name, state.get("ticker"))
        return {}

    ticker = state["ticker"]
    logger.info("Running %s for %s @ %s (holding %s @ %.2f from %s)",
                agent_name, ticker, state["as_of_date"],
                position.get("quantity"), position.get("avg_price", 0),
                position.get("entry_date", "?"))

    # ── Broker pump/circular rule-based override ─────────────────────
    # Terminal's exit rule (simple_backtest.py:2297-2306): if the position
    # has been open >=5 bars AND today's floorsheet flags a pump or
    # circular pattern, exit at market — skip the LLM. This is a NEPSE-
    # specific edge Argos otherwise misses. Silently no-ops when the
    # sibling broker_signals SQLite is unreachable.
    bars_held = int(position.get("bars_held", 0))
    pump_check = should_pump_exit(ticker, state["as_of_date"], bars_held)
    if pump_check is not None:
        pump_fires, pump_reason = pump_check
        if pump_fires:
            logger.info("%s SHORT-CIRCUIT (pump/circular) %s: %s",
                        agent_name, ticker, pump_reason)
            return {
                "exit_decision": {
                    "action": "exit_at_market",
                    "confidence": 0.75,
                    "reasoning": f"Broker-flow override: {pump_reason}",
                    "new_stop_pct": None,
                },
            }

    # Recompute the snapshot for context (may share cache with technicals node)
    ohlcv = (state.get("ohlcv_window") or {}).get("ohlcv") or []
    if not ohlcv:
        logger.warning("%s: no ohlcv — cannot evaluate exit", agent_name)
        return {}
    snap = compute_snapshot(ohlcv, ticker, state["as_of_date"])
    kronos = state.get("kronos_forecast") or {}

    system_prompt = _load_prompt().format(
        ticker=ticker, as_of_date=state["as_of_date"],
        position_json=json.dumps(position, default=str),
    )
    user_message = (
        f"Existing LONG position in {ticker}:\n"
        f"  entry_price = {position.get('avg_price')}\n"
        f"  quantity    = {position.get('quantity')}\n"
        f"  market_price= {position.get('market_price', 'n/a')}\n"
        f"  unrealized  = {position.get('unrealized_pnl', 'n/a')}\n\n"
        f"{snap.to_prompt_text()}\n\n"
        f"### Kronos (calibrated)\n"
        f"direction_score={kronos.get('direction_score')}, "
        f"short_horizon_pct={kronos.get('short_horizon_pct')}, "
        f"shape={kronos.get('shape_regime')}, "
        f"uncertainty={kronos.get('avg_uncertainty_normalized')}\n\n"
        f"Return your JSON exit verdict."
    )

    try:
        llm = get_llm(agent_name)
        structured = llm.with_structured_output(ExitDecision)
        result: ExitDecision = structured.invoke([
            ("system", system_prompt), ("user", user_message),
        ])
        decision = result.model_dump()
        logger.info("%s → %s (conf %.2f)  %s",
                    agent_name, decision["action"], decision["confidence"],
                    (decision.get("reasoning") or "")[:80])

        existing = dict(state.get("analyst_prompts") or {})
        return {
            "exit_decision": decision,
            "analyst_prompts": {**existing, "exit_manager": {
                "system": system_prompt, "user": user_message,
            }},
        }
    except Exception as e:  # noqa: BLE001
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        # Default to hold_position on error — safest
        return {
            "exit_decision": {
                "action": "hold_position",
                "confidence": 0.0,
                "reasoning": f"Exit manager errored: {e}",
                "new_stop_pct": None,
            },
            "errors": [error_msg],
        }


def should_short_circuit_exit(state: FuseState) -> str:
    """Conditional edge after exit_manager.

    If the exit manager said `exit_at_market`, jump straight to the
    portfolio manager to emit a SELL and skip the whole debate — we're
    closing, not opening. Otherwise proceed with the analyst fan-out.
    """
    exit_d = state.get("exit_decision") or {}
    if exit_d.get("action") == "exit_at_market":
        return "exit_now"
    return "continue"
