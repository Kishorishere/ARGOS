"""Read-only decision-journal API.

  GET /api/journals?market=nasdaq|nepse|legacy   — ticker → dates tree
  GET /api/journals/{market}/{ticker}/{date}      — attempts (latest | all)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from server.services import journal_reader

router = APIRouter(prefix="/api/journals", tags=["journals"])


@router.get("")
def journal_tree(market: str = Query("nasdaq")) -> dict:
    try:
        return journal_reader.list_market_journals(market)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{market}/{ticker}/{date}")
def journal_attempts(
    market: str,
    ticker: str,
    date: str,
    attempts: str = Query("latest", pattern="^(latest|all)$"),
) -> dict:
    if market not in journal_reader.MARKETS:
        raise HTTPException(status_code=400, detail=f"market must be one of {journal_reader.MARKETS}")
    all_attempts = journal_reader.read_attempts(market, ticker, date)
    if not all_attempts:
        raise HTTPException(status_code=404, detail=f"no journal for {ticker} @ {date} ({market})")
    picked = all_attempts if attempts == "all" else all_attempts[-1:]
    path = journal_reader.journal_path(market, ticker, date)
    return {
        "market": market,
        "ticker": ticker.upper(),
        "date": date,
        "path": str(path.relative_to(journal_reader.REPO_ROOT)) if path else None,
        "n_attempts": len(all_attempts),
        "attempts": picked,
    }
