"""NEPSE corporate actions calendar (P7).

Purpose:
- Prevent knife-catch losers around ex-dates (typical NEPSE stock drops
  2-4% on ex-dividend / ex-bonus and 5-10% on ex-rights day).
- Enable dividend_ex_reversion strategy (5-day post-ex rebound has
  documented ~55% hit rate on Nepal Commercial Banks).
- Give the Risk Manager real event context ("book close in 3 days —
  entry blocked unless holding through").

Data model — one entry per (symbol, action_type, book_close_date):

    {
      "symbol": "NABIL",
      "action_type": "cash_dividend" | "bonus" | "rights" | "agm",
      "book_close_date": "2024-12-15",
      "ex_date": "2024-12-16",           # T+1 for cash dividend
      "record_date": "2024-12-15",
      "payment_date": "2024-12-25",      # if known
      "cash_dividend_pct": 12.5,         # percent of paid-up capital
      "bonus_share_pct": 5.0,
      "rights_ratio": "10:1",            # 10 existing → 1 new
      "rights_price_npr": 100,
      "declared_at": "2024-11-20",       # AGM/board declaration date
      "source": "sharesansar" | "merolagani" | "manual",
      "notes": "",
    }

Storage: `data/calendar/corporate_actions.jsonl` (append-only).

Scraper is a stub for now — the primary ShareSansar AJAX endpoint
returned empty results in dev, and the fallback path via HTML tables
requires session-cookie flow. Real scraping can be added later; the
infrastructure lets you populate manually today by editing the JSONL.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

CALENDAR_PATH = Path(__file__).resolve().parents[2] / "data" / "calendar" / "corporate_actions.jsonl"

ActionType = Literal["cash_dividend", "bonus", "rights", "agm", "special"]


@dataclass
class CorporateAction:
    symbol: str
    action_type: ActionType
    book_close_date: str          # YYYY-MM-DD
    ex_date: Optional[str] = None
    record_date: Optional[str] = None
    payment_date: Optional[str] = None
    cash_dividend_pct: float = 0.0
    bonus_share_pct: float = 0.0
    rights_ratio: str = ""
    rights_price_npr: float = 0.0
    declared_at: str = ""
    source: str = "manual"
    notes: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def load_all() -> list[CorporateAction]:
    """Load every calendar entry from the JSONL file."""
    if not CALENDAR_PATH.exists():
        return []
    out: list[CorporateAction] = []
    for line in CALENDAR_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(CorporateAction(**json.loads(line)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping malformed calendar line: %s", e)
    return out


def append(action: CorporateAction) -> None:
    """Append a single calendar entry, deduping on (symbol, action_type, book_close)."""
    CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = load_all()
    key = (action.symbol, action.action_type, action.book_close_date)
    for e in existing:
        if (e.symbol, e.action_type, e.book_close_date) == key:
            logger.info("Calendar entry already exists — skipping duplicate: %s", key)
            return
    with open(CALENDAR_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(action.to_dict()) + "\n")


def actions_for_symbol(symbol: str) -> list[CorporateAction]:
    return [a for a in load_all() if a.symbol.upper() == symbol.upper()]


def upcoming_actions(as_of_date: str, window_days: int = 14) -> list[CorporateAction]:
    """All actions with book_close_date in [as_of, as_of + window_days]."""
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    horizon = as_of + timedelta(days=window_days)
    out = []
    for a in load_all():
        try:
            bc = datetime.strptime(a.book_close_date, "%Y-%m-%d").date()
            if as_of <= bc <= horizon:
                out.append(a)
        except ValueError:
            continue
    return sorted(out, key=lambda a: a.book_close_date)


def days_to_next_action(symbol: str, as_of_date: str) -> Optional[dict]:
    """Return the nearest upcoming corporate action for `symbol` after
    `as_of_date`, with `days_until` field, or None if nothing on file.

    Used by risk_manager to block entries near ex-dates.
    """
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    upcoming = []
    for a in actions_for_symbol(symbol):
        try:
            bc = datetime.strptime(a.book_close_date, "%Y-%m-%d").date()
            if bc >= as_of:
                upcoming.append((bc, a))
        except ValueError:
            continue
    if not upcoming:
        return None
    upcoming.sort(key=lambda x: x[0])
    bc, a = upcoming[0]
    return {
        "symbol": a.symbol,
        "action_type": a.action_type,
        "book_close_date": a.book_close_date,
        "days_until": (bc - as_of).days,
        "cash_dividend_pct": a.cash_dividend_pct,
        "bonus_share_pct": a.bonus_share_pct,
        "rights_ratio": a.rights_ratio,
        "notes": a.notes,
    }


# ── Stub scraper (real implementation TBD) ──────────────────────────────

def fetch_from_nepse_api() -> list[CorporateAction]:
    """Pull dividend + book-closure records from the nepse.nandisolutions.com
    /api/nepse/dividends feed and persist as CorporateAction rows.

    Returns the list of newly appended actions.
    """
    from argos.data.nepse_api import fetch_dividends
    try:
        raw = fetch_dividends()
    except Exception as e:  # noqa: BLE001
        logger.warning("Dividend API fetch failed: %s", e)
        return []
    appended: list[CorporateAction] = []
    for d in raw:
        symbol = (d.get("symbol") or "").upper()
        if not symbol:
            continue
        cash = float(d.get("cash") or 0)
        bonus = float(d.get("bonus") or 0)
        # Book-closure date may be blank on the API for proposed items
        book_close = d.get("bookClosureDateAD") or ""
        # If no book close date but we have fiscal year, skip storage
        # (we can't compute days_until without it) — but keep record with
        # today's date as a lightweight "declared" marker
        if not book_close:
            book_close = date.today().isoformat()
            action_notes = "book_close TBD (API blank)"
        else:
            action_notes = ""
        # Determine primary action_type
        if bonus > 0 and cash > 0:
            action_type = "cash_dividend"  # store the cash side; bonus captured in bonus_share_pct
        elif bonus > 0:
            action_type = "bonus"
        elif cash > 0:
            action_type = "cash_dividend"
        else:
            action_type = "special"
        ca = CorporateAction(
            symbol=symbol,
            action_type=action_type,
            book_close_date=book_close,
            cash_dividend_pct=cash,
            bonus_share_pct=bonus,
            declared_at=d.get("declaredAt") or date.today().isoformat(),
            source="nepse_nandisolutions",
            notes=(d.get("companyName") or "") + (" | " + action_notes if action_notes else ""),
        )
        append(ca)
        appended.append(ca)
    logger.info("Ingested %d corporate actions from NEPSE API", len(appended))
    return appended


# Alias for backward compat with earlier stub name
fetch_from_sharesansar = fetch_from_nepse_api
