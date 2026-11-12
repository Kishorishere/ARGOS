"""Override log API.

  POST /api/overrides       — log a user override of a committee call
  GET  /api/overrides       — recent overrides (optionally per-ticker)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.services.overrides import list_overrides, record_override

router = APIRouter(prefix="/api/overrides", tags=["overrides"])


class OverrideBody(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    committee_action: str | None = None
    committee_confidence: float | None = None
    user_action: str = Field(..., min_length=1, max_length=20)
    user_reason: str = Field(..., min_length=1, max_length=2000)
    run_id: str | None = None
    attempt_id: str | None = None
    journal_path: str | None = None


@router.post("")
def post_override(body: OverrideBody) -> dict:
    try:
        row = record_override(**body.model_dump())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"stored": row}


@router.get("")
def get_overrides(
    ticker: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> dict:
    rows = list_overrides(limit=limit, ticker=ticker)
    return {"n": len(rows), "overrides": rows}
