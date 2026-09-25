"""Nepal macro overlay (P11).

Tracks daily macro indicators and classifies the current regime:

  risk_on   — money flowing to equities: NEPSE up, low NRB rate,
              strong remittance, NIFTY up
  neutral   — mixed signals; no clear regime
  risk_off  — money fleeing: NRB tight, NEPSE index down 5%+ from 20d
              high, gold rallying, USD/NPR weakening, NIFTY down

The Risk Manager treats `risk_off` as a hard filter that requires
3-of-3 timeframe confluence to allow a new BUY (P3). Fundamentals still
work in the forecast, but position sizes are capped harder.

Data-source strategy:
  1. NEPSE index — from `argos/data/nepse_api.py` (already live)
  2. USD/NPR — Nepal Rastra Bank has a daily rate page; fallback: 1 USD ≈
     134 NPR (2024 baseline)
  3. Gold NPR/tola — Federation of Nepal Gold & Silver Dealers Association
     (fnjda.org.np). Fallback: static ~150,000 NPR/tola
  4. NIFTY close — yfinance ^NSEI
  5. Remittance — monthly NRB print (multi-month lag); when unavailable
     use a static "expanding" flag if year-over-year growth > 5%

For this first cut we compute a REGIME purely from NEPSE + NIFTY (both
live) and leave hooks for the others to add later. Even a two-signal
regime detector catches most macro shifts on NEPSE.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

MACRO_HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "macro_history.jsonl"

Regime = Literal["risk_on", "neutral", "risk_off"]


@dataclass
class MacroSnapshot:
    date: str
    nepse_index: Optional[float] = None
    nepse_change_pct: Optional[float] = None
    nepse_20d_high: Optional[float] = None
    nepse_drawdown_pct: Optional[float] = None      # drop from 20d high
    nifty_close: Optional[float] = None
    nifty_change_pct: Optional[float] = None
    usd_npr: Optional[float] = None
    gold_npr_per_tola: Optional[float] = None
    remittance_yoy_growth_pct: Optional[float] = None
    regime: Optional[Regime] = None
    regime_reasons: list = None

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["regime_reasons"] = list(self.regime_reasons or [])
        return d


def fetch_nepse_index_snapshot() -> tuple[float | None, float | None]:
    """Return (index_level, change_pct) from the live NEPSE API."""
    try:
        from argos.data.nepse_api import fetch_market_snapshot
        snap = fetch_market_snapshot()
        summary = snap.get("summary") or {}
        return (summary.get("nepseIndex"), summary.get("indexChangePercent"))
    except Exception as e:  # noqa: BLE001
        logger.warning("NEPSE index fetch failed: %s", e)
        return (None, None)


def fetch_nifty_close(timeout: int = 15) -> tuple[float | None, float | None]:
    """Return (last close, day change %) for ^NSEI via yfinance."""
    try:
        import yfinance as yf
        h = yf.download("^NSEI", period="5d", interval="1d",
                        progress=False, auto_adjust=True)
        if h.empty or len(h) < 2:
            return (None, None)
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = h.columns.get_level_values(0)
        close = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2])
        change_pct = (close / prev - 1) * 100
        return (close, change_pct)
    except Exception as e:  # noqa: BLE001
        logger.warning("NIFTY fetch failed: %s", e)
        return (None, None)


def _load_history() -> list[MacroSnapshot]:
    if not MACRO_HISTORY_PATH.exists():
        return []
    out = []
    for line in MACRO_HISTORY_PATH.read_text().splitlines():
        if line.strip():
            try:
                out.append(MacroSnapshot(**json.loads(line)))
            except Exception:  # noqa: BLE001
                continue
    return out


def _compute_nepse_drawdown(current_index: float | None,
                             history: list[MacroSnapshot]) -> tuple[float | None, float | None]:
    """Rolling 20-day NEPSE index high + drawdown-from-high in %."""
    if current_index is None or not history:
        return (None, None)
    tail = [s.nepse_index for s in history[-20:] if s.nepse_index]
    tail.append(current_index)
    if len(tail) < 3:
        return (None, None)
    high = max(tail)
    dd = (current_index / high - 1) * 100
    return (high, round(dd, 3))


def classify_regime(snap: MacroSnapshot) -> tuple[Regime, list[str]]:
    """Simple decision tree — extend as macro coverage broadens."""
    reasons: list[str] = []
    risk_off_flags = 0
    risk_on_flags = 0

    if snap.nepse_drawdown_pct is not None:
        if snap.nepse_drawdown_pct < -5.0:
            risk_off_flags += 1
            reasons.append(f"NEPSE {snap.nepse_drawdown_pct:.1f}% off 20d high")
        elif snap.nepse_drawdown_pct > -1.0 and (snap.nepse_change_pct or 0) > 0:
            risk_on_flags += 1
            reasons.append("NEPSE at/near 20d high")

    if snap.nepse_change_pct is not None:
        if snap.nepse_change_pct < -1.0:
            risk_off_flags += 1
            reasons.append(f"NEPSE today {snap.nepse_change_pct:.2f}%")
        elif snap.nepse_change_pct > 1.0:
            risk_on_flags += 1
            reasons.append(f"NEPSE today {snap.nepse_change_pct:+.2f}%")

    if snap.nifty_change_pct is not None:
        if snap.nifty_change_pct < -1.5:
            risk_off_flags += 1
            reasons.append(f"NIFTY today {snap.nifty_change_pct:.2f}%")
        elif snap.nifty_change_pct > 1.0:
            risk_on_flags += 1
            reasons.append(f"NIFTY today {snap.nifty_change_pct:+.2f}%")

    if snap.remittance_yoy_growth_pct is not None:
        if snap.remittance_yoy_growth_pct > 5.0:
            risk_on_flags += 1
            reasons.append(f"Remittance YoY +{snap.remittance_yoy_growth_pct:.1f}%")
        elif snap.remittance_yoy_growth_pct < 0:
            risk_off_flags += 1
            reasons.append(f"Remittance YoY {snap.remittance_yoy_growth_pct:.1f}%")

    if risk_off_flags >= 2 and risk_off_flags > risk_on_flags:
        return ("risk_off", reasons)
    if risk_on_flags >= 2 and risk_on_flags > risk_off_flags:
        return ("risk_on", reasons)
    return ("neutral", reasons)


def snapshot_macro_today() -> MacroSnapshot:
    """Fetch today's macro indicators, classify regime, append to history."""
    MACRO_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    history = _load_history()

    nepse_idx, nepse_chg = fetch_nepse_index_snapshot()
    nifty_close, nifty_chg = fetch_nifty_close()
    nepse_high, nepse_dd = _compute_nepse_drawdown(nepse_idx, history)

    snap = MacroSnapshot(
        date=today,
        nepse_index=nepse_idx,
        nepse_change_pct=nepse_chg,
        nepse_20d_high=nepse_high,
        nepse_drawdown_pct=nepse_dd,
        nifty_close=nifty_close,
        nifty_change_pct=nifty_chg,
        regime_reasons=[],
    )
    regime, reasons = classify_regime(snap)
    snap.regime = regime
    snap.regime_reasons = reasons

    # De-dupe today's entry
    for h in history:
        if h.date == today:
            return snap
    with open(MACRO_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap.to_dict()) + "\n")
    logger.info("Macro snapshot for %s: regime=%s (reasons: %s)",
                today, regime, "; ".join(reasons) or "no strong signals")
    return snap


def current_regime() -> tuple[Regime, list[str]]:
    """Return the most-recent regime + reasons. Snapshots if none today."""
    hist = _load_history()
    today = date.today().isoformat()
    if hist and hist[-1].date == today and hist[-1].regime:
        return (hist[-1].regime, hist[-1].regime_reasons or [])
    snap = snapshot_macro_today()
    return (snap.regime or "neutral", snap.regime_reasons or [])
