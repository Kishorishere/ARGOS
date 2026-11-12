"""Raw-data snapshot API.

  GET /api/snapshot/{ticker}?as_of=YYYY-MM-DD

Returns price + indicators + fundamentals + portfolio exposure + freshness
for one ticker. No LLM calls, <2s warm (yfinance-bound cold).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from server.services import snapshot as snapshot_svc

router = APIRouter(prefix="/api/snapshot", tags=["snapshot"])


@router.get("/{ticker}")
def ticker_snapshot(ticker: str, as_of: str | None = Query(None)) -> dict:
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="ticker required")
    try:
        return snapshot_svc.build_snapshot(ticker.upper(), as_of)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
