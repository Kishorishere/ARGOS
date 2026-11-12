"""GET /api/system — terminal status: cache, Kronos, market, journal counts."""
from __future__ import annotations

import os

from fastapi import APIRouter

from argos.llm_cache import cache_stats
from argos.market import current_market, market_label
from server.services import journal_reader

router = APIRouter(prefix="/api/system", tags=["system"])


def _kronos_status() -> dict:
    url = os.environ.get("KRONOS_ENDPOINT_URL", "").strip()
    if not url:
        return {"configured": False, "reachable": False, "detail": "KRONOS_ENDPOINT_URL not set"}
    try:
        from argos.data.kronos_client import check_health

        health = check_health(url, timeout=5)
        return {"configured": True, "reachable": True, "endpoint": url, **health}
    except Exception as e:  # noqa: BLE001
        return {"configured": True, "reachable": False, "endpoint": url, "detail": str(e)}


def _journal_counts() -> dict:
    out: dict[str, dict] = {}
    for market in journal_reader.MARKETS:
        try:
            tree = journal_reader.list_market_journals(market)
        except Exception as e:  # noqa: BLE001
            out[market] = {"error": str(e)}
            continue
        tickers = tree["tickers"]
        out[market] = {
            "tickers": len(tickers),
            "records": sum(t["records"] for t in tickers),
            "errors": sum(t["errors"] for t in tickers),
            "dates": sum(len(t["dates"]) for t in tickers),
        }
    return out


@router.get("")
def system_status() -> dict:
    return {
        "service": "argos-terminal",
        "market": current_market(),
        "market_label": market_label(),
        "max_debate_rounds": int(os.environ.get("MAX_DEBATE_ROUNDS", "2")),
        "llm_cache": cache_stats(),
        "kronos": _kronos_status(),
        "journals": _journal_counts(),
    }
