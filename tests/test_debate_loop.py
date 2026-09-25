"""Tests for the multi-round debate routing wired in argos/graph.py."""

from __future__ import annotations

import pytest

from argos.graph import should_debate_again


def _st(round_: int, max_: int) -> dict:
    return {"debate_round": round_, "max_debate_rounds": max_}


class TestShouldDebateAgain:
    def test_default_route_loops_when_below_max(self):
        # After bear's round-1 turn, bear_node increments to 2. max=2 => still bull_again
        assert should_debate_again(_st(round_=2, max_=2)) == "bull_again"

    def test_routes_done_when_exceeded_max(self):
        # After bear's round-2 turn, round becomes 3. max=2 => done
        assert should_debate_again(_st(round_=3, max_=2)) == "done"

    def test_max_one_round_exits_immediately_after_first_pair(self):
        # After first bear turn, round=2, max=1 => done
        assert should_debate_again(_st(round_=2, max_=1)) == "done"

    def test_high_max_keeps_looping(self):
        for r in range(2, 5):
            assert should_debate_again(_st(round_=r, max_=5)) == "bull_again"
        assert should_debate_again(_st(round_=6, max_=5)) == "done"

    def test_missing_state_defaults_to_env(self, monkeypatch):
        monkeypatch.setenv("MAX_DEBATE_ROUNDS", "3")
        # No max_debate_rounds in state — should read env
        assert should_debate_again({"debate_round": 2}) == "bull_again"
        assert should_debate_again({"debate_round": 4}) == "done"


def test_bear_node_increments_debate_round():
    """bear_node returns debate_round: current + 1 in its output dict.

    We check via a monkey-patched LLM so we don't need a live Gemini call.
    """
    from argos.nodes import debate as debate_mod

    class FakeLLM:
        def with_structured_output(self, schema):
            self._schema = schema
            return self

        def invoke(self, messages):
            return self._schema(
                position="sell",
                strongest_evidence="ev",
                rebuttal_of_opposing_view="reb",
                confidence=0.5,
                reasoning="r",
                evidence_summary="",
            )

    def fake_get_llm(agent):
        return FakeLLM()

    debate_mod.get_llm = fake_get_llm

    state = {
        "ticker": "AAPL",
        "as_of_date": "2024-01-15",
        "debate_round": 1,
        "bull_argument": {
            "position": "buy", "confidence": 0.7, "reasoning": "r",
            "strongest_evidence": "e", "rebuttal_of_opposing_view": "reb",
            "evidence_summary": "",
        },
        "fundamentals_signal": None,
        "sentiment_signal": None,
        "technicals_signal": None,
        "kronos_forecast": None,
        "debate_transcript": [],
        "analyst_prompts": {},
    }
    result = debate_mod.bear_node(state)
    assert result["debate_round"] == 2, "bear must advance the round counter"
    assert len(result["debate_transcript"]) == 1
    assert result["debate_transcript"][0]["side"] == "bear"
    assert result["debate_transcript"][0]["round"] == 1
