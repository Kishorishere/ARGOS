"""Tests for the MARKET env flag and NEPSE-feed gating in the ensemble."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def _reload_market_module():
    import importlib
    import argos.market as m
    importlib.reload(m)
    return m


def test_market_defaults_to_nasdaq():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MARKET", None)
        m = _reload_market_module()
        assert m.current_market() == "nasdaq"
        assert m.is_nasdaq() is True
        assert m.is_nepse() is False


def test_market_nepse_toggles():
    with patch.dict(os.environ, {"MARKET": "nepse"}):
        m = _reload_market_module()
        assert m.current_market() == "nepse"
        assert m.is_nepse() is True
        assert m.is_nasdaq() is False


def test_market_invalid_value_raises():
    with patch.dict(os.environ, {"MARKET": "nyse"}):
        m = _reload_market_module()
        with pytest.raises(ValueError, match="MARKET="):
            m.current_market()


def test_market_case_insensitive():
    with patch.dict(os.environ, {"MARKET": "NASDAQ"}):
        m = _reload_market_module()
        assert m.current_market() == "nasdaq"


def test_ensemble_skips_nepse_sources_when_nasdaq():
    """The ensemble must not fetch floorsheet / broker / sebon / Nepali
    sentiment when MARKET=nasdaq. We only check that the extract path
    runs without raising — a NEPSE-feed call would error hard because
    the data file doesn't exist for NVDA."""
    with patch.dict(os.environ, {"MARKET": "nasdaq"}):
        _reload_market_module()
        # Reload ensemble so its top-level `from argos.market import is_nepse`
        # picks up the fresh module reference.
        import importlib
        import argos.ensemble.sources as sources
        importlib.reload(sources)

        state = {
            "ticker": "NVDA",
            "as_of_date": "2024-06-03",
            "ohlcv_window": {"ohlcv": []},
            "technicals_signal": None,
            "kronos_forecast": None,
            "bull_argument": None,
            "bear_argument": None,
            "judge_verdict": None,
        }
        # Should complete without raising even though NVDA has no NEPSE data.
        contribs = sources.extract_sources(state, horizon="1d")
        assert isinstance(contribs, list)
