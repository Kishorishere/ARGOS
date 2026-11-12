"""Ownership API.

  GET /api/ownership/{ticker}?limit=N — insider Form 4 + 13F placeholder
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from server.services.ownership import ownership_block

router = APIRouter(prefix="/api/ownership", tags=["ownership"])


@router.get("/{ticker}")
def ticker_ownership(ticker: str, limit: int = Query(10, ge=1, le=50)) -> dict:
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="ticker required")
    return ownership_block(ticker.upper(), limit=limit)
