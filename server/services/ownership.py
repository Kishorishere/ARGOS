"""Ownership snapshot — composite of insider Form 4 + 13F placeholder."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def ownership_block(ticker: str, limit: int = 10) -> dict:
    out: dict = {"ticker": ticker.upper()}

    try:
        from argos.data.edgar import recent_form4s, ticker_to_cik, top_institutional_holders
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"edgar_import_failed: {e}"}

    cik: Optional[int] = ticker_to_cik(ticker)
    out["cik"] = cik
    if cik is None:
        out["error"] = f"cik_not_found: {ticker}"
        return out

    try:
        events = recent_form4s(ticker, limit=limit)
    except Exception as e:  # noqa: BLE001
        events = []
        out["form4_error"] = f"form4_fetch_failed: {e}"
    out["form4"] = [e.to_dict() for e in events]

    try:
        holders = top_institutional_holders(ticker, limit=10)
    except Exception as e:  # noqa: BLE001
        holders = []
        out["holders_error"] = f"holders_fetch_failed: {e}"
    out["holders"] = [h.to_dict() for h in holders]
    if not holders and not out.get("holders_error"):
        out["holders_note"] = (
            "13F holder inversion pending — Phase C.2 weekly offline scrape."
        )

    return out
