"""Triggers API.

  GET /api/triggers/{ticker}?as_of=YYYY-MM-DD   — fired triggers for one ticker
  GET /api/triggers                             — scan every watchlist ticker

The Live view in the frontend polls the second route; the Events band on
the ticker page uses the first. Both are read-only and cheap by design
(no LLM calls). yfinance scheduled lookups are the slowest path — expect
<1s per ticker warm, ~2s cold.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from argos.triggers import check_all

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_PATH = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"

router = APIRouter(prefix="/api/triggers", tags=["triggers"])


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_universe() -> list[str]:
    if not UNIVERSE_PATH.exists():
        return []
    try:
        return list(json.loads(UNIVERSE_PATH.read_text()).get("tickers") or [])
    except Exception:  # noqa: BLE001
        return []


@router.get("")
def list_triggers(as_of: str | None = Query(None)) -> dict:
    """Scan the watchlist universe for fired triggers. Returns one entry
    per ticker with its fired triggers (empty list when nothing fires)."""
    as_of = as_of or _today_iso()
    out: list[dict] = []
    for ticker in _load_universe():
        try:
            fires = [t.to_dict() for t in check_all(ticker, as_of)]
        except Exception as e:  # noqa: BLE001
            out.append({"ticker": ticker, "error": str(e), "triggers": []})
            continue
        out.append({"ticker": ticker, "n": len(fires), "triggers": fires})
    return {
        "as_of": as_of,
        "universe_path": str(UNIVERSE_PATH.relative_to(REPO_ROOT)) if UNIVERSE_PATH.exists() else None,
        "n_tickers": len(out),
        "tickers": out,
    }


@router.get("/{ticker}")
def ticker_triggers(ticker: str, as_of: str | None = Query(None)) -> dict:
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="ticker required")
    as_of = as_of or _today_iso()
    try:
        fires = [t.to_dict() for t in check_all(ticker.upper(), as_of)]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "ticker": ticker.upper(),
        "as_of": as_of,
        "n": len(fires),
        "triggers": fires,
    }
