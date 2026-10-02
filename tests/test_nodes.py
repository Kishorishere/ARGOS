"""
Unit tests for Argos agent schemas and state creation.

These tests use fixture data, NOT live Gemini calls — they're fast and free.
"""

import json
from pathlib import Path

import pytest

from argos.state import FuseState
from argos.schemas import AnalystSignal, DebateArgument, RiskVerdict, FinalDecision
from argos.model_router import AGENT_MODEL_MAP, get_model_config
from argos.graph import create_initial_state


FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ── Fixture loading ──────────────────────────────────────────────────────

@pytest.fixture
def sample_state() -> FuseState:
    """Load a sample state from the fixture file."""
    fixture_path = FIXTURE_DIR / "sample_state.json"
    with open(fixture_path) as f:
        return json.load(f)


# ── Schema validation tests ──────────────────────────────────────────────

class TestAnalystSignal:
    """Test the AnalystSignal Pydantic schema."""

    def test_valid_bullish(self):
        signal = AnalystSignal(
            signal="bullish",
            confidence=0.85,
            key_factors=["Strong revenue growth", "Beat earnings estimates"],
            reasoning="Company shows strong fundamentals with 15% YoY revenue growth."
        )
        assert signal.signal == "bullish"
        assert signal.confidence == 0.85
        assert len(signal.key_factors) == 2

    def test_valid_neutral(self):
        signal = AnalystSignal(
            signal="neutral",
            confidence=0.3,
            key_factors=["Mixed signals"],
            reasoning="Unclear direction."
        )
        assert signal.signal == "neutral"

    def test_invalid_confidence_above_1(self):
        with pytest.raises(Exception):
            AnalystSignal(
                signal="bullish",
                confidence=1.5,
                key_factors=["test"],
                reasoning="test"
            )

    def test_invalid_confidence_below_0(self):
        with pytest.raises(Exception):
            AnalystSignal(
                signal="bearish",
                confidence=-0.1,
                key_factors=["test"],
                reasoning="test"
            )

    def test_invalid_signal_value(self):
        with pytest.raises(Exception):
            AnalystSignal(
                signal="super-bullish",
                confidence=0.5,
                key_factors=["test"],
                reasoning="test"
            )

    def test_key_factors_max_length(self):
        with pytest.raises(Exception):
            AnalystSignal(
                signal="bullish",
                confidence=0.5,
                key_factors=["a", "b", "c", "d", "e", "f"],  # 6 > max_length=5
                reasoning="test"
            )


class TestDebateArgument:
    """Test the DebateArgument Pydantic schema."""

    def test_valid_bull_argument(self):
        arg = DebateArgument(
            position="buy",
            strongest_evidence="Revenue beat estimates by 12%",
            rebuttal_of_opposing_view="Bear's concern about margins is outdated",
            confidence=0.75,
            reasoning="Strong Q4 results with accelerating growth."
        )
        assert arg.position == "buy"

    def test_valid_bear_argument(self):
        arg = DebateArgument(
            position="sell",
            strongest_evidence="Declining user growth",
            rebuttal_of_opposing_view="Bull ignores the competitive pressure from new entrants",
            confidence=0.6,
            reasoning="Market saturation is real."
        )
        assert arg.position == "sell"


class TestRiskVerdict:
    """Test the RiskVerdict Pydantic schema."""

    def test_valid_approved(self):
        verdict = RiskVerdict(
            veto=False,
            max_position_size=0.05,
            reasoning="Acceptable risk given tight Kronos uncertainty bands.",
            risk_factors=["Market volatility"]
        )
        assert verdict.veto is False
        assert verdict.max_position_size == 0.05

    def test_valid_vetoed(self):
        verdict = RiskVerdict(
            veto=True,
            max_position_size=0.0,
            reasoning="Kronos uncertainty too wide. Veto.",
            risk_factors=["Wide uncertainty", "Mixed debate signals"]
        )
        assert verdict.veto is True
        assert verdict.max_position_size == 0.0


class TestFinalDecision:
    """Test the FinalDecision Pydantic schema."""

    def test_valid_buy(self):
        decision = FinalDecision(
            action="buy",
            confidence=0.72,
            position_size=0.03,
            rationale="Bull case outweighs bear.",
            dissent_notes=""
        )
        assert decision.action == "buy"

    def test_valid_hold(self):
        decision = FinalDecision(
            action="hold",
            confidence=0.0,
            position_size=0.0,
            rationale="Risk Manager vetoed.",
            dissent_notes="Risk Manager exercised veto."
        )
        assert decision.action == "hold"


# ── Model router tests ───────────────────────────────────────────────────

class TestModelRouter:
    """Test the model routing configuration."""

    def test_all_agents_registered(self):
        # judge is the U1 blind-judge added after bull/bear
        expected_agents = {
            "fundamentals", "sentiment", "technicals",
            "bull", "bear", "judge", "exit_manager", "risk_manager", "portfolio_manager"
        }
        assert set(AGENT_MODEL_MAP.keys()) == expected_agents

    def test_grounded_analysts_flag_grounded(self):
        # Fundamentals + sentiment need live-web grounding. After the NASDAQ
        # pivot the primary is OpenAI (Responses-API web_search_preview);
        # Gemini/Google-Search remains in the fallback chain.
        for agent in ("fundamentals", "sentiment"):
            cfg = get_model_config(agent)
            assert cfg.provider == "openai", f"{agent} primary must be OpenAI"
            assert cfg.grounded is True
            # Fallback chain must include at least one grounded backup so a
            # primary outage doesn't silently drop grounding.
            assert any(fb.grounded for fb in cfg.fallbacks), \
                f"{agent} has no grounded fallback (chain={[fb.provider for fb in cfg.fallbacks]})"

    def test_debate_symmetry(self):
        """Bull and Bear share the primary model — the load-bearing invariant
        after the 44-date NEPSE backtest surfaced the model-prior confidence gap."""
        b = get_model_config("bull")
        x = get_model_config("bear")
        assert b.provider == x.provider
        assert b.model_name == x.model_name

    def test_every_agent_has_a_fallback_chain(self):
        # Every agent must have at least one fallback so a primary outage
        # doesn't silently drop the node.
        for agent, cfg in AGENT_MODEL_MAP.items():
            assert len(cfg.fallbacks) >= 1, f"{agent} has no fallbacks configured"

    def test_unknown_agent_raises_keyerror(self):
        with pytest.raises(KeyError):
            get_model_config("nonexistent_agent")


# ── State creation tests ─────────────────────────────────────────────────

class TestStateCreation:
    """Test initial state creation."""

    def test_creates_valid_state(self):
        state = create_initial_state("AAPL", "2024-03-04")
        assert state["ticker"] == "AAPL"
        assert state["as_of_date"] == "2024-03-04"
        assert state["run_id"] is not None
        assert state["errors"] == []

    def test_all_none_signals_at_start(self):
        state = create_initial_state("MSFT", "2024-06-15")
        assert state["fundamentals_signal"] is None
        assert state["sentiment_signal"] is None
        assert state["technicals_signal"] is None
        assert state["kronos_forecast"] is None
        assert state["bull_argument"] is None
        assert state["bear_argument"] is None
        assert state["risk_verdict"] is None
        assert state["final_decision"] is None

    def test_fixture_loads_correctly(self, sample_state: FuseState):
        assert sample_state["ticker"] == "AAPL"
        assert sample_state["as_of_date"] == "2024-03-04"
        assert sample_state["run_id"] == "test-run-001"
        assert len(sample_state["ohlcv_window"]["ohlcv"]) == 3
