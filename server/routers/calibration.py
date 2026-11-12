"""Calibration API.

  GET /api/calibration?horizon=5 — reliability curve for committee confidence
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from server.services.calibration import reliability_curve

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.get("")
def get_calibration(horizon: int = Query(5)) -> dict:
    return reliability_curve(horizon)
