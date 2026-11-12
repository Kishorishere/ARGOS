"""Live analyze endpoint — run the committee on today's data.

  POST /api/analyze/{ticker}         → kicks off a live run, returns {run_id}
  GET  /api/analyze/{run_id}/stream  → SSE live event stream (identical to /api/runs/{id}/stream)

A wrapper around server.services.runner.start_run that fills in sensible
live-mode defaults (as_of=today, live=True) and routes the journal to
logs/decisions/live/{market}/{ticker}/. The actual SSE stream is already
served by /api/runs/{id}/stream — the analyze endpoint reuses the same hub.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.services.runner import start_run

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class AnalyzeRequest(BaseModel):
    market: str = Field("nasdaq", pattern="^(nasdaq|nepse)$")
    as_of: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    max_debate_rounds: int | None = Field(None, ge=1, le=10)
    persist: bool = True


@router.post("/{ticker}")
def start_analyze(ticker: str, req: AnalyzeRequest | None = None) -> dict:
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="ticker required")
    req = req or AnalyzeRequest()
    as_of = req.as_of or _today_iso()
    try:
        run_id = start_run({
            "market": req.market,
            "ticker": ticker.upper(),
            "date": as_of,
            "max_debate_rounds": req.max_debate_rounds,
            "persist": req.persist,
            "live": as_of == _today_iso(),
        })
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "run_id": run_id,
        "ticker": ticker.upper(),
        "as_of": as_of,
        "live": as_of == _today_iso(),
        "stream_url": f"/api/runs/{run_id}/stream",
    }
