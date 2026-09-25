"""Tests for the P26-P31 flow + broker + announcement + beta signals.

All tests are deterministic — no HTTP, no live NEPSE snapshot. Where a
signal calls out to `as_of_today`/`fetch_market_snapshot`/`companies_by_symbol`
we monkey-patch it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────


def _write_floorsheet(tmp_path: Path, d: str, rows: list[dict]) -> None:
    p = tmp_path / f"{d}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _row(symbol: str, buy: str, sell: str, qty: int = 100, price: float = 100.0) -> dict:
    return {
        "date": "2026-09-24",
        "symbol": symbol,
        "buyer_broker_id": buy,
        "seller_broker_id": sell,
        "quantity": qty,
        "price": price,
        "amount": qty * price,
    }


def _ohlcv(n: int = 30, base: float = 100.0, drift: float = 0.5,
           vol: int = 10_000) -> list[dict]:
    """Deterministic OHLCV: base + drift * i, constant volume unless overridden."""
    out = []
    start = date(2026, 8, 1)
    for i in range(n):
        p = base + drift * i
        out.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "open": p, "high": p * 1.01, "low": p * 0.99, "close": p,
            "volume": vol,
        })
    return out


# ── flow_analytics: wash_trade_ratio ────────────────────────────────────


def test_wash_trade_ratio_clean_market(tmp_path, monkeypatch):
    from argos.data import flow_analytics

    monkeypatch.setattr(flow_analytics, "FLOORSHEET_DIR", tmp_path)
    _write_floorsheet(tmp_path, "2026-09-24", [
        _row("NABIL", "1", "2"),
        _row("NABIL", "3", "4"),
        _row("NABIL", "5", "6"),
    ])
    assert flow_analytics.wash_trade_ratio("NABIL", "2026-09-24") == 0.0


def test_wash_trade_ratio_all_self_cross(tmp_path, monkeypatch):
    from argos.data import flow_analytics

    monkeypatch.setattr(flow_analytics, "FLOORSHEET_DIR", tmp_path)
    _write_floorsheet(tmp_path, "2026-09-24", [
        _row("NABIL", "77", "77"),
        _row("NABIL", "77", "77"),
    ])
    assert flow_analytics.wash_trade_ratio("NABIL", "2026-09-24") == 1.0


def test_wash_trade_ratio_partial(tmp_path, monkeypatch):
    from argos.data import flow_analytics

    monkeypatch.setattr(flow_analytics, "FLOORSHEET_DIR", tmp_path)
    _write_floorsheet(tmp_path, "2026-09-24", [
        _row("NABIL", "1", "2", qty=100),   # 10k NPR clean
        _row("NABIL", "3", "3", qty=100),   # 10k NPR self-cross
    ])
    assert flow_analytics.wash_trade_ratio("NABIL", "2026-09-24") == 0.5


def test_wash_trade_ratio_no_data(tmp_path, monkeypatch):
    from argos.data import flow_analytics

    monkeypatch.setattr(flow_analytics, "FLOORSHEET_DIR", tmp_path)
    assert flow_analytics.wash_trade_ratio("NABIL", "2026-09-24") is None


# ── flow_analytics: circuit_breaker_proximity ───────────────────────────


def test_circuit_proximity_near_upper_limit(monkeypatch):
    from argos.data import flow_analytics

    monkeypatch.setattr(
        flow_analytics, "as_of_today", lambda syms: {"X": {"changePercent": 9.5}},
        raising=False,
    )
    # Need to monkey the import inside the function; patch the module attr
    import argos.data.nepse_api as api
    monkeypatch.setattr(api, "as_of_today", lambda syms: {"X": {"changePercent": 9.5}})
    signal = flow_analytics.circuit_breaker_proximity("X")
    # At +9.5% we're just at the caution edge (upper − caution_band = 8.5),
    # so signal should be strongly negative (mean-revert bearish)
    assert signal < 0
    assert -1.0 <= signal <= 1.0


def test_circuit_proximity_near_lower_limit(monkeypatch):
    from argos.data import flow_analytics
    import argos.data.nepse_api as api

    monkeypatch.setattr(api, "as_of_today", lambda syms: {"X": {"changePercent": -9.8}})
    signal = flow_analytics.circuit_breaker_proximity("X")
    assert signal > 0                # mean-revert bullish
    assert -1.0 <= signal <= 1.0


def test_circuit_proximity_mid_range(monkeypatch):
    import argos.data.nepse_api as api
    from argos.data import flow_analytics

    monkeypatch.setattr(api, "as_of_today", lambda syms: {"X": {"changePercent": 2.0}})
    assert flow_analytics.circuit_breaker_proximity("X") == 0.0


# ── flow_analytics: volume_price_divergence ─────────────────────────────


def test_vpd_bullish_rally_confirmed_volume(monkeypatch):
    """Price up + volume up → no divergence, signal = 0."""
    from argos.data.flow_analytics import volume_price_divergence

    rec = _ohlcv(n=25, base=100, drift=0.5, vol=10_000)
    # Amp last 5 sessions' volume
    for r in rec[-5:]:
        r["volume"] = 20_000
    assert volume_price_divergence("X", rec) == 0.0


def test_vpd_bullish_rally_light_volume_fade(monkeypatch):
    """Price rose but recent volume shrunk → bearish fade signal."""
    from argos.data.flow_analytics import volume_price_divergence

    rec = _ohlcv(n=25, base=100, drift=1.0, vol=20_000)
    for r in rec[-5:]:
        r["volume"] = 5_000        # 75% below prior avg
    sig = volume_price_divergence("X", rec)
    assert sig is not None and sig < 0    # fade the up-move


def test_vpd_declining_light_volume_bounce(monkeypatch):
    """Price fell on light volume → bullish bounce signal."""
    from argos.data.flow_analytics import volume_price_divergence

    rec = _ohlcv(n=25, base=100, drift=-1.0, vol=20_000)
    for r in rec[-5:]:
        r["volume"] = 5_000
    sig = volume_price_divergence("X", rec)
    assert sig is not None and sig > 0


def test_vpd_flat_price_no_signal():
    from argos.data.flow_analytics import volume_price_divergence

    rec = _ohlcv(n=25, base=100, drift=0.0, vol=10_000)
    assert volume_price_divergence("X", rec) == 0.0


# ── broker_patterns ─────────────────────────────────────────────────────


def test_top_buyer_streak_dominant(monkeypatch, tmp_path):
    from argos.data import broker_patterns

    monkeypatch.setattr(broker_patterns, "FLOORSHEET_DIR", tmp_path)
    for d in ["2026-09-24", "2026-09-23", "2026-09-22", "2026-09-21", "2026-09-20"]:
        _write_floorsheet(tmp_path, d, [
            _row("NABIL", "77", "12", qty=1000),  # broker 77 dominant buyer
            _row("NABIL", "77", "13", qty=500),
            _row("NABIL", "22", "77", qty=200),
        ])
    streak = broker_patterns.top_buyer_streak("NABIL", "2026-09-24", days=5)
    assert streak["signal"] > 0.5
    assert streak["broker"] == "77"


def test_top_buyer_streak_no_dominance(monkeypatch, tmp_path):
    from argos.data import broker_patterns

    monkeypatch.setattr(broker_patterns, "FLOORSHEET_DIR", tmp_path)
    # Both sides varied every day → no persistence on either side
    for i, d in enumerate(["2026-09-24", "2026-09-23", "2026-09-22",
                            "2026-09-21", "2026-09-20"]):
        _write_floorsheet(tmp_path, d, [
            _row("NABIL", str(10 + i), str(30 + i), qty=1000),
        ])
    assert broker_patterns.top_buyer_streak("NABIL", "2026-09-24")["signal"] == 0.0


# ── announcement_reaction ───────────────────────────────────────────────


def test_announcement_drift_bullish_underreaction(monkeypatch):
    """News score +0.6, price flat → bullish drift signal."""
    from argos.data import announcement_reaction

    monkeypatch.setattr(
        "argos.data.announcements.announcement_score",
        lambda s: 0.6, raising=False,
    )
    ohlcv = _ohlcv(n=10, base=100, drift=0.0, vol=10_000)   # flat 5-day
    sig = announcement_reaction.announcement_price_gap("NABIL", "2026-09-24", ohlcv)
    assert sig is not None and sig > 0


def test_announcement_drift_already_reacted(monkeypatch):
    """News score +0.6, price already moved ~+3% → gap small."""
    from argos.data import announcement_reaction

    monkeypatch.setattr(
        "argos.data.announcements.announcement_score",
        lambda s: 0.6, raising=False,
    )
    ohlcv = _ohlcv(n=10, base=100, drift=0.6, vol=10_000)   # +3% over 5d
    sig = announcement_reaction.announcement_price_gap("NABIL", "2026-09-24", ohlcv)
    # Expected: 0.6 * 2.5 = 1.5%, realized ~3% → over-reacted → negative
    assert sig is not None and sig < 0.2


def test_announcement_drift_no_news(monkeypatch):
    from argos.data import announcement_reaction

    monkeypatch.setattr(
        "argos.data.announcements.announcement_score",
        lambda s: 0.0, raising=False,
    )
    ohlcv = _ohlcv(n=10)
    assert announcement_reaction.announcement_price_gap("NABIL", "2026-09-24", ohlcv) == 0.0


# ── beta_adjusted ───────────────────────────────────────────────────────


def test_beta_calc_lockstep_series():
    """Stock and index moving in exact lockstep → beta ≈ 1."""
    from argos.data.beta_adjusted import rolling_beta

    closes = [100 + i for i in range(30)]  # steady rise
    beta = rolling_beta(closes, closes)     # identical series
    assert beta is not None and 0.95 < beta < 1.05


def test_beta_adjusted_signal_needs_history(monkeypatch, tmp_path):
    """Without accumulated NEPSE index history, signal returns None."""
    from argos.data import beta_adjusted

    fake_path = tmp_path / "sector_history.jsonl"
    monkeypatch.setattr(beta_adjusted, "SECTOR_HISTORY_PATH", fake_path)
    ohlcv = _ohlcv(n=60)
    assert beta_adjusted.beta_adjusted_return_signal("NABIL", ohlcv) is None


def test_beta_adjusted_signal_positive_alpha(monkeypatch, tmp_path):
    """Stock outperforms index today → positive alpha signal."""
    from argos.data import beta_adjusted

    fake_path = tmp_path / "sector_history.jsonl"
    # 30 sessions of NEPSE index history, aligned to ohlcv dates
    dates = [(date(2026, 8, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    entries = []
    for i, d in enumerate(dates):
        entries.append({
            "date": d,
            "sectors": [{"name": "NEPSE Index", "index": 2000 + i * 0.5}],
        })
    fake_path.write_text("\n".join(json.dumps(e) for e in entries))
    monkeypatch.setattr(beta_adjusted, "SECTOR_HISTORY_PATH", fake_path)

    # Stock rose in lockstep with index, but big jump on the last day
    ohlcv = _ohlcv(n=30, base=100, drift=0.05, vol=10_000)
    ohlcv[-1]["close"] = ohlcv[-2]["close"] * 1.03    # +3% today
    sig = beta_adjusted.beta_adjusted_return_signal("NABIL", ohlcv, lookback=30)
    assert sig is not None and sig > 0
