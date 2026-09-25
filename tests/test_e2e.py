"""End-to-end graph test with a mocked Gemini and synthetic OHLCV.

Runs the full Argos LangGraph pipeline — fundamentals, sentiment,
technicals (with real IndicatorSnapshot), bull, bear (multi-round),
risk manager, portfolio manager — with a canned LLM to keep the test
offline and fast. Asserts:
  - graph invokes to completion
  - IndicatorSnapshot is attached to the technicals signal
  - debate_transcript has both a bull and a bear turn per round
  - final_decision is present and Pydantic-valid shape
  - veto short-circuit produces HOLD
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from argos import model_router
from argos.graph import compile_graph, create_initial_state
from argos.schemas import (
    AnalystSignal,
    DebateArgument,
    FinalDecision,
    JudgeVerdict,
    RiskVerdict,
)


# ── Synthetic OHLCV ────────────────────────────────────────────────────────


def _synthetic_ohlcv(n: int = 120) -> dict:
    rng = np.random.default_rng(11)
    close = 100 + rng.normal(0, 0.5, n).cumsum() * 0.3
    high = close + rng.uniform(0.1, 0.6, n)
    low = close - rng.uniform(0.1, 0.6, n)
    open_ = close + rng.normal(0, 0.2, n)
    dates = pd.bdate_range("2024-01-02", periods=n).strftime("%Y-%m-%d")
    return {
        "ticker": "AAPL",
        "as_of_date": dates[-1],
        "ohlcv": [
            {"date": dates[i], "open": float(open_[i]), "high": float(high[i]),
             "low": float(low[i]), "close": float(close[i]),
             "volume": int(100_000 + i * 500)}
            for i in range(n)
        ],
        "rows": n,
        "date_range": f"{dates[0]} to {dates[-1]}",
    }


# ── Canned LLM ─────────────────────────────────────────────────────────────


class CannedLLM:
    """A stand-in for ChatGoogleGenerativeAI that returns fixture responses."""

    def __init__(self, schema_responses: dict):
        self._schema_responses = schema_responses
        self._schema = None

    def with_structured_output(self, schema):
        self._schema = schema
        return self

    def bind(self, **kwargs):
        return self

    def invoke(self, messages):
        assert self._schema is not None, "with_structured_output not called first"
        response = self._schema_responses.get(self._schema)
        if response is None:
            raise KeyError(f"No canned response for schema {self._schema}")
        return response


def _canned_llm_factory(veto: bool = False):
    """Return a `get_llm(agent_name)` that hands back a canned LLM.

    U9 update: to force a hard veto in tests we now need BOTH the LLM to
    veto AND at least one rule vote to fire (2/4 threshold). The `veto=True`
    fixture below sets bull/bear confidences low + close so the debate_mixed
    rule also fires — making the total 2/4 votes → final veto.
    """
    analyst = AnalystSignal(
        signal="bullish", confidence=0.7,
        key_factors=["RSI 32 oversold", "MACD hist flipped up"],
        reasoning="Momentum turning up with volume confirmation.",
    )
    if veto:
        # Weak debate to trigger debate_mixed rule vote
        debate = DebateArgument(
            position="buy", strongest_evidence="Weak evidence",
            rebuttal_of_opposing_view="Not strong",
            confidence=0.32, reasoning="Marginal.",
            evidence_summary="thin",
        )
    else:
        debate = DebateArgument(
            position="buy", strongest_evidence="RSI 32 with bullish engulfing",
            rebuttal_of_opposing_view="Bear ignores the volume spike",
            confidence=0.65, reasoning="Weight of evidence leans bull.",
            evidence_summary="RSI=32, vol_z=2.1",
        )
    judge = JudgeVerdict(
        winner="A", confidence=0.55,
        reasoning="Argument A cites more indicator values.",
        key_factor="RSI oversold quoted with exact value",
    )
    risk = RiskVerdict(
        veto=veto,
        max_position_size=0.0 if veto else 0.05,
        reasoning="Uncertainty too wide" if veto else "Acceptable risk.",
        risk_factors=["Kronos wide"] if veto else ["Sector concentration ok"],
    )
    final = FinalDecision(
        action="buy", confidence=0.62, position_size=0.04,
        rationale="Bull thesis outweighs bear.",
        dissent_notes="",
    )
    canned = {AnalystSignal: analyst, DebateArgument: debate,
              JudgeVerdict: judge, RiskVerdict: risk, FinalDecision: final}

    def factory(agent_name):
        return CannedLLM(canned)

    return factory


# ── Tests ──────────────────────────────────────────────────────────────────


class TestGraphE2E:
    def test_full_run_with_canned_llm(self, monkeypatch):
        # Patch every node's `get_llm` at its import site
        from argos.nodes import (
            fundamentals as fmod, sentiment as smod, technicals as tmod,
            debate as dmod, judge as jmod, risk_manager as rmod, portfolio_manager as pmod,
        )
        factory = _canned_llm_factory(veto=False)
        for m in (fmod, smod, tmod, dmod, jmod, rmod, pmod):
            monkeypatch.setattr(m, "get_llm", factory)

        state = create_initial_state("AAPL", "2024-05-01", ohlcv_window=_synthetic_ohlcv(120))
        # Force MAX_DEBATE_ROUNDS=1 to keep the test tiny
        state["max_debate_rounds"] = 1
        graph = compile_graph()
        result = graph.invoke(state)

        # Analysts fired
        assert result["fundamentals_signal"]["signal"] == "bullish"
        assert result["sentiment_signal"]["signal"] == "bullish"
        assert result["technicals_signal"]["signal"] == "bullish"

        # Indicator snapshot is attached to the technicals signal
        snap = result["technicals_signal"]["indicator_snapshot"]
        assert snap is not None
        assert "fired_signals" in snap
        assert "trend" in snap and "momentum" in snap
        assert snap["bars_used"] == 120

        # Debate transcript has bull AND bear
        transcript = result["debate_transcript"]
        assert any(t["side"] == "bull" for t in transcript)
        assert any(t["side"] == "bear" for t in transcript)

        # Risk approved and portfolio manager produced a real decision
        assert result["risk_verdict"]["veto"] is False
        assert result["final_decision"]["action"] in ("buy", "sell", "hold")
        assert 0.0 <= result["final_decision"]["confidence"] <= 1.0

    def test_veto_short_circuits_to_hold(self, monkeypatch):
        from argos.nodes import (
            fundamentals as fmod, sentiment as smod, technicals as tmod,
            debate as dmod, judge as jmod, risk_manager as rmod, portfolio_manager as pmod,
        )
        # veto=True fixture triggers LLM veto + debate_mixed (both conf 0.32).
        # As of P4/P5/P11 the vote panel is 7 votes, threshold 3. To force
        # a final veto in tests we also make the ticker's sector heavily
        # concentrated in the portfolio → sector_cap vote fires.
        factory = _canned_llm_factory(veto=True)
        for m in (fmod, smod, tmod, dmod, jmod, rmod, pmod):
            monkeypatch.setattr(m, "get_llm", factory)

        state = create_initial_state("AAPL", "2024-05-01", ohlcv_window=_synthetic_ohlcv(120))
        state["max_debate_rounds"] = 1
        # Portfolio: AAPL already 40% of a "Tech" sector we're overweight
        state["portfolio_state"] = {
            "cash": 60000.0, "total_equity": 100000.0, "cash_pct": 60.0,
            "gross_exposure_pct": 40.0, "day_pnl": 0.0, "day_pnl_pct": 0.0,
            "n_positions": 1, "positions": [
                {"ticker": "AAPL", "sector": "Tech", "weight_pct": 40.0, "unrealized_pnl": 0.0},
            ],
            "sector_weights_pct": {"Tech": 40.0},
        }
        graph = compile_graph()
        result = graph.invoke(state)

        assert result["risk_verdict"]["veto"] is True
        assert result["risk_verdict"]["veto_count"] >= 3, "expect 3+ votes in expanded panel"
        assert result["final_decision"]["action"] == "hold"
        assert result["final_decision"]["confidence"] == 0.0
