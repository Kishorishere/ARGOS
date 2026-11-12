"""OHLCV chart feed.

  GET /api/ohlcv/{ticker}?as_of=YYYY-MM-DD&lookback_days=N

Thin wrapper around argos.data.ohlcv_loader. Exists so the frontend chart
doesn't have to re-run the full snapshot (indicators etc.) just to redraw
candles on a timeframe change. Live by default when as_of == today.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/ohlcv", tags=["ohlcv"])


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@router.get("/{ticker}")
def ohlcv(
    ticker: str,
    as_of: str | None = Query(None),
    lookback_days: int = Query(180, ge=5, le=1825),
) -> dict:
    as_of = as_of or _today_iso()
    live = as_of == _today_iso()
    try:
        from argos.data.ohlcv_loader import load_ohlcv
        window = load_ohlcv(ticker.upper(), as_of,
                            lookback_days=lookback_days, allow_today=live)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "ticker": ticker.upper(),
        "as_of": as_of,
        "live": live,
        "lookback_days": lookback_days,
        "n_bars": window.get("rows", len(window.get("ohlcv") or [])),
        "date_range": window.get("date_range"),
        "ohlcv": window.get("ohlcv") or [],
    }
