"""GET /api/reports/summary — eval summary.json passthrough (or [] when absent)."""
from __future__ import annotations

import json

from fastapi import APIRouter

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
SUMMARY_PATH = REPO_ROOT / "reports" / "nasdaq_focus" / "summary.json"

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/summary")
def eval_summary() -> dict:
    if not SUMMARY_PATH.exists():
        return {"path": None, "tickers": []}
    with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "path": str(SUMMARY_PATH.relative_to(REPO_ROOT)),
        "tickers": data if isinstance(data, list) else [],
    }
