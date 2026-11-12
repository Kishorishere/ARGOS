"""Live run API.

  POST /api/runs                 — start a pipeline run → {run_id}
  GET  /api/runs                 — list runs (newest first)
  GET  /api/runs/{id}            — run status snapshot
  GET  /api/runs/{id}/stream     — SSE: node-by-node replay + live tail
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from server.services.runner import start_run
from server.services.stream_hub import hub

router = APIRouter(prefix="/api/runs", tags=["runs"])

_HEARTBEAT = 15.0


class RunRequest(BaseModel):
    market: str = Field("nasdaq", pattern="^(nasdaq|nepse)$")
    ticker: str = Field(..., min_length=1, max_length=12)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    max_debate_rounds: int | None = Field(None, ge=1, le=10)
    persist: bool = True


@router.post("")
def create_run(req: RunRequest) -> dict:
    try:
        run_id = start_run(
            {
                "market": req.market,
                "ticker": req.ticker.upper(),
                "date": req.date,
                "max_debate_rounds": req.max_debate_rounds,
                "persist": req.persist,
            }
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"run_id": run_id, "status": "queued"}


@router.get("")
def list_runs() -> dict:
    return {"runs": hub.list()}


@router.get("/{run_id}")
def run_status(run_id: str) -> dict:
    st = hub.get(run_id)
    if st is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return {
        "run_id": st.run_id,
        "params": st.params,
        "status": st.status,
        "started_at": st.started_at,
        "n_events": len(st.events),
    }


def _sse(ev: dict) -> str:
    if ev.get("seq", -1) < 0:  # heartbeat marker from the hub
        return f": ping {int(time.time())}\n\n"
    payload = json.dumps(ev["data"], default=str)
    return f"event: {ev['event']}\ndata: {payload}\n\n"


@router.get("/{run_id}/stream")
def stream_run(run_id: str) -> StreamingResponse:
    if hub.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")

    def gen():
        for ev in hub.sse_events(run_id):
            yield _sse(ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
