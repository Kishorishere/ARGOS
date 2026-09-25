"""Tests for argos.journal — append-only writes, attempt_ids, indicator trace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argos.journal import (
    append_record,
    build_record,
    date_already_journalled,
    _flatten_indicator_signals,
    _indicator_composite,
)


def _fake_state() -> dict:
    return {
        "run_id": "run-1",
        "fundamentals_signal": {"signal": "bullish", "confidence": 0.6},
        "sentiment_signal": {"signal": "neutral", "confidence": 0.4},
        "technicals_signal": {
            "signal": "bullish",
            "confidence": 0.7,
            "indicator_snapshot": {
                "composite_bull_score": 3.5,
                "composite_bear_score": 1.0,
                "net_bias_score": 2.5,
                "trend": {"regime": "uptrend"},
                "volatility": {"vol_regime": "mid"},
                "candles": {"bull_ringer_score": 1.5, "bear_ringer_score": 0.0},
                "fired_signals": [
                    {"indicator": "RSI(14)", "value": 27.4, "threshold": "<30",
                     "direction": "bullish", "weight": 1.5, "note": "oversold"},
                    {"indicator": "MACD hist", "value": -0.02, "threshold": "<0",
                     "direction": "bearish", "weight": 1.0, "note": "below"},
                ],
            },
        },
        "kronos_forecast": None,
        "bull_argument": {"position": "buy", "confidence": 0.72},
        "bear_argument": {"position": "sell", "confidence": 0.35},
        "debate_round": 3,
        "debate_transcript": [
            {"round": 1, "side": "bull"}, {"round": 1, "side": "bear"},
            {"round": 2, "side": "bull"}, {"round": 2, "side": "bear"},
        ],
        "risk_verdict": {"veto": False, "max_position_size": 0.05},
        "final_decision": {"action": "buy", "confidence": 0.65, "position_size": 0.03},
        "analyst_prompts": {"fundamentals": {"system": "s", "user": "u"}},
        "portfolio_state": {"cash_pct": 100.0, "positions": []},
        "errors": [],
    }


def test_flatten_indicator_signals_contribution_sign():
    sigs = _flatten_indicator_signals(_fake_state()["technicals_signal"])
    assert len(sigs) == 2
    rsi = next(s for s in sigs if s["indicator"] == "RSI(14)")
    macd = next(s for s in sigs if s["indicator"] == "MACD hist")
    assert rsi["contribution"] == 1.5
    assert macd["contribution"] == -1.0


def test_indicator_composite_keys():
    comp = _indicator_composite(_fake_state()["technicals_signal"])
    assert comp["trend_regime"] == "uptrend"
    assert comp["vol_regime"] == "mid"
    assert comp["net_bias_score"] == 2.5


def test_build_record_has_expected_keys():
    rec = build_record("AAPL", "2024-01-15", _fake_state(), {"rows": 60}, 12.34)
    required = {
        "attempt_id", "attempted_at", "argos_version", "duration_s",
        "ticker", "date", "ohlcv_summary", "portfolio_state",
        "analyst_signals", "kronos_forecast",
        "indicator_composite", "indicator_signals_fired",
        "debate", "debate_transcript", "risk_verdict",
        "final_decision", "analyst_prompts", "errors",
    }
    missing = required - set(rec)
    assert not missing, f"journal record missing keys: {missing}"
    assert rec["debate"]["rounds_completed"] == 2


def test_append_and_reappend_produce_unique_attempt_ids(tmp_path: Path):
    ticker, date = "TICK", "2024-05-01"
    rec1 = build_record(ticker, date, _fake_state(), {"rows": 30}, 1.0)
    rec2 = build_record(ticker, date, _fake_state(), {"rows": 30}, 2.0)
    p = append_record(rec1, tmp_path, ticker, date)
    assert date_already_journalled(tmp_path, ticker, date)
    append_record(rec2, tmp_path, ticker, date)

    lines = p.read_text().splitlines()
    assert len(lines) == 2
    ids = {json.loads(l)["attempt_id"] for l in lines}
    assert len(ids) == 2
