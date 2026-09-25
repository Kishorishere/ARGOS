"""
Risk Manager node.

Reviews the Bull/Bear debate, has UNCONDITIONAL VETO AUTHORITY.
When vetoed, the graph short-circuits — Portfolio Manager is skipped and
final_decision is set directly to HOLD.
"""

from __future__ import annotations

import logging
from pathlib import Path

from argos.model_router import get_llm
from argos.schemas import RiskVerdict
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "risk_manager.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text()


def _format_prompt(state: FuseState) -> str:
    return _load_prompt().format(
        ticker=state["ticker"],
        as_of_date=state["as_of_date"],
    )


def risk_manager_node(state: FuseState) -> dict:
    """Run the Risk Manager agent."""
    agent_name = "risk_manager"
    logger.info(
        "Running %s for %s as of %s",
        agent_name,
        state["ticker"],
        state["as_of_date"],
    )

    try:
        llm = get_llm(agent_name)
        structured_llm = llm.with_structured_output(RiskVerdict)
        system_prompt = _format_prompt(state)

        # Build context from debate outputs
        bull = state.get("bull_argument", {})
        bear = state.get("bear_argument", {})
        portfolio = state.get("portfolio_state", {})

        # Format debate transcript for context
        transcript = state.get("debate_transcript", [])
        transcript_text = ""
        if transcript:
            parts = []
            for entry in transcript:
                side = entry.get("side", "?").upper()
                pos = entry.get("position", "?")
                conf = entry.get("confidence", 0)
                evidence = entry.get("strongest_evidence", "")
                parts.append(f"[{side}] position={pos} confidence={conf:.2f}\n  Evidence: {evidence}")
            transcript_text = "\n\n".join(parts)

        user_message = (
            f"Risk review for {state['ticker']} as of {state['as_of_date']}.\n\n"
            f"## Bull Argument\n{bull}\n\n"
            f"## Bear Argument\n{bear}\n\n"
            f"## Debate Transcript\n{transcript_text or '(No transcript available)'}\n\n"
            f"## Portfolio State\n{portfolio or '(No portfolio state — first run)'}\n\n"
            f"Assess risk and render your verdict."
        )

        result: RiskVerdict = structured_llm.invoke([
            ("system", system_prompt),
            ("user", user_message),
        ])

        verdict_dict = result.model_dump()

        # ── U9 + P5 + P7: augmented rule-voted veto ────────────────────
        # Vote panel now has 6 checks: LLM, mixed-debate, vol-ambiguity,
        # sector-cap, market-breadth, corporate-action-nearby. Threshold
        # scales with panel size (~1/3 must veto) — corporate-action is
        # always a HARD single-vote veto because ex-dates are certain.
        votes = _compute_rule_votes(state, verdict_dict)
        veto_count = sum(1 for v in votes if v["veto"])
        veto_threshold = _threshold_for(votes)
        corporate_action_veto = any(
            v["name"] == "corporate_action_nearby" and v["veto"] for v in votes
        )
        final_veto = veto_count >= veto_threshold or corporate_action_veto
        verdict_dict["veto"] = final_veto
        verdict_dict["veto_votes"] = votes
        verdict_dict["veto_count"] = veto_count

        if not final_veto and result.veto:
            # LLM said veto but rules cleared → keep LLM's max_position_size cap
            # but allow the trade. Log the disagreement.
            logger.info("%s: LLM voted VETO but %d/4 rule votes agreed → trade proceeds (soft-vetoed)",
                        agent_name, veto_count)
        if final_veto and not result.veto:
            logger.info("%s: LLM approved but %d/4 rule votes triggered → HARD VETO",
                        agent_name, veto_count)
            verdict_dict["max_position_size"] = 0.0

        logger.info(
            "%s verdict: veto=%s (votes=%d/4), max_size=%.2f",
            agent_name, verdict_dict["veto"], veto_count, verdict_dict["max_position_size"],
        )

        existing_prompts = dict(state.get("analyst_prompts") or {})
        return {
            "risk_verdict": verdict_dict,
            "analyst_prompts": {
                **existing_prompts,
                "risk_manager": {"system": system_prompt, "user": user_message},
            },
        }

    except Exception as e:
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {
            "risk_verdict": None,
            "errors": [error_msg],
        }


def _compute_rule_votes(state: FuseState, llm_verdict: dict) -> list[dict]:
    """Four independent, cheap veto checks that run alongside the LLM.

    Each returns {name, veto, reason}. The final veto is majority-based
    rather than LLM-dictated — kills the "one grumpy LLM = no trade" failure
    mode seen in the 44-date backtest.
    """
    votes: list[dict] = []

    # Vote 1: LLM verdict itself
    votes.append({
        "name": "llm",
        "veto": bool(llm_verdict.get("veto")),
        "reason": (llm_verdict.get("reasoning") or "")[:120],
    })

    # Vote 2: mixed debate signal — bull and bear both weak & close in confidence
    bull = (state.get("bull_argument") or {}).get("confidence", 0.0)
    bear = (state.get("bear_argument") or {}).get("confidence", 0.0)
    gap = abs(bull - bear)
    mixed = (bull < 0.5 and bear < 0.5) or (gap < 0.15 and max(bull, bear) < 0.65)
    votes.append({
        "name": "debate_mixed",
        "veto": mixed,
        "reason": f"bull={bull:.2f} bear={bear:.2f} gap={gap:.2f}",
    })

    # Vote 3: volatile regime + weak conviction — refuse to trade when
    # volatility is high AND the composite bias is small.
    ts = state.get("technicals_signal") or {}
    snap = ts.get("indicator_snapshot") or {}
    vol_regime = (snap.get("volatility") or {}).get("vol_regime")
    net_bias = abs(snap.get("net_bias_score") or 0.0)
    vol_ambiguous = (vol_regime == "high" and net_bias < 2.0)
    votes.append({
        "name": "vol_ambiguity",
        "veto": vol_ambiguous,
        "reason": f"vol_regime={vol_regime} |net_bias|={net_bias:.2f}",
    })

    # Vote 4: sector-concentration cap — refuse if this ticker's sector is
    # already >30% of portfolio equity.
    ps = state.get("portfolio_state") or {}
    sector_weights = ps.get("sector_weights_pct") or {}
    # Find the sector for this ticker from the position list, if we hold it
    ticker = state.get("ticker", "")
    ticker_sector = None
    for pos in ps.get("positions") or []:
        if pos.get("ticker") == ticker:
            ticker_sector = pos.get("sector")
            break
    concentrated = ticker_sector and sector_weights.get(ticker_sector, 0.0) > 30.0
    votes.append({
        "name": "sector_cap",
        "veto": bool(concentrated),
        "reason": (f"sector={ticker_sector} weight={sector_weights.get(ticker_sector, 0):.1f}%"
                   if ticker_sector else "no existing sector exposure"),
    })

    # Vote 5 (P11): macro regime hostile.
    # In risk_off regime, BUYs need 3/3 timeframe confluence. Any weaker
    # buy setup in risk_off = veto vote. SELLs are unimpeded (defensive
    # regime = short-friendly). Neutral/risk_on regimes never veto here.
    macro_veto = False
    macro_reason = "no macro data"
    try:
        from argos.data.macro_context import current_regime
        regime, reasons = current_regime()
    except Exception as e:  # noqa: BLE001
        regime, reasons = ("neutral", [f"macro fetch failed: {e}"])
    net_bias_val = (snap.get("net_bias_score") or 0.0) if snap else 0.0
    if regime == "risk_off" and net_bias_val > 0:
        n_bull_tf = (confluence or {}).get("n_bullish", 0)
        if n_bull_tf < 3:
            macro_veto = True
            macro_reason = (f"risk_off regime; BUY needs 3/3 TF confluence "
                            f"(got {n_bull_tf}/3). Reasons: {'; '.join(reasons[:2])}")
        else:
            macro_reason = f"risk_off but 3/3 TF bullish — allowed"
    else:
        macro_reason = f"regime={regime}"
    votes.append({
        "name": "macro_regime",
        "veto": macro_veto,
        "reason": macro_reason,
    })

    # Vote 6 (P5): market breadth hostile to direction.
    # Pull today's live NEPSE breadth. If A/D ratio < 0.3 (broad-market
    # rout) AND we're proposing a BUY without strong confluence, veto.
    # Mirror for very positive breadth + SELL.
    try:
        from argos.data.nepse_api import market_breadth
        breadth = market_breadth()
    except Exception:
        breadth = None
    ts = state.get("technicals_signal") or {}
    snap = ts.get("indicator_snapshot") or {}
    confluence = snap.get("confluence") or {}
    n_bull_tf = confluence.get("n_bullish", 0)
    n_bear_tf = confluence.get("n_bearish", 0)
    ad_ratio = (breadth or {}).get("advance_decline_ratio")
    net_bias = snap.get("net_bias_score") or 0.0
    breadth_veto = False
    breadth_reason = "no breadth data"
    if ad_ratio is not None:
        # If proposing a BUY (net_bias>0) in broad rout AND < 3/3 TF confluence, veto
        if net_bias > 0 and ad_ratio < 0.3 and n_bull_tf < 3:
            breadth_veto = True
            breadth_reason = (f"A/D ratio {ad_ratio:.2f} hostile to BUY "
                              f"(bull_tf {n_bull_tf}/3)")
        elif net_bias < 0 and ad_ratio > 3.0 and n_bear_tf < 3:
            breadth_veto = True
            breadth_reason = (f"A/D ratio {ad_ratio:.2f} hostile to SELL "
                              f"(bear_tf {n_bear_tf}/3)")
        else:
            breadth_reason = f"A/D {ad_ratio:.2f} not hostile"
    votes.append({
        "name": "market_breadth",
        "veto": breadth_veto,
        "reason": breadth_reason,
    })

    # Vote 7 (P7): corporate action within 3 days — block new entries.
    # Book-close windows systematically drop 2-4% (dividend) to 5-10%
    # (rights) on ex-date. If we're entering close to an ex-date, we're
    # buying a known price drop unless we hold through the ex.
    try:
        from argos.data.corporate_actions import days_to_next_action
        next_action = days_to_next_action(ticker, state.get("as_of_date", ""))
    except Exception:
        next_action = None
    if next_action and next_action["days_until"] <= 3:
        votes.append({
            "name": "corporate_action_nearby",
            "veto": True,
            "reason": (f"{next_action['action_type']} in {next_action['days_until']}d "
                       f"(book_close {next_action['book_close_date']}, "
                       f"div_pct={next_action['cash_dividend_pct']}%)"),
        })
    else:
        votes.append({
            "name": "corporate_action_nearby",
            "veto": False,
            "reason": (f"next action in {next_action['days_until']}d ({next_action['action_type']})"
                       if next_action else "no upcoming action within 14d"),
        })

    return votes


def _threshold_for(votes: list[dict]) -> int:
    """Number of vetoes required for a final veto.

    Threshold scales with the number of live votes so adding new checks
    (P5 breadth was #5, P7 corp actions is now #6) doesn't tighten the
    barrier accidentally. Threshold = ceil(n/3).
    """
    return max(2, (len(votes) + 2) // 3)


def should_veto(state: FuseState) -> str:
    """Conditional edge: honors the augmented (rule-voted) verdict."""
    risk_verdict = state.get("risk_verdict")
    if risk_verdict and risk_verdict.get("veto", False):
        logger.info("Risk Manager VETOED (%d/4 votes) — short-circuiting to HOLD",
                    risk_verdict.get("veto_count", "?"))
        return "vetoed"
    return "approved"
