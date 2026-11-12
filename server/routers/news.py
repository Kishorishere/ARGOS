"""News API.

  GET /api/news/{ticker}?days=7&limit=20 — Finnhub company-news
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/{ticker}")
def ticker_news(
    ticker: str,
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="ticker required")
    from argos.data.news import recent_news
    return recent_news(ticker.upper(), days=days, limit=limit)
