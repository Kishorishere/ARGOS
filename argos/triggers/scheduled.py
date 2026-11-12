"""Scheduled-event triggers — things we know are coming on the calendar.

Phase B implements earnings proximity via yfinance's `.calendar` attribute
(the lightest-weight way to get a next-earnings date without a paid feed).
Ex-dividend proximity uses the same source. Fed / CPI / OPEX calendars are
deferred to a later phase — they belong to a different kind of trigger
(market-wide, not per-ticker) and need their own pinned JSON.

Every rule in this module is defensive: if yfinance returns nothing (new
ticker, rate-limited, free-tier flake), the module returns [] silently
rather than raising. A trader would rather see "no scheduled triggers"
than a stack trace in the dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime

from argos.triggers.base import Trigger

logger = logging.getLogger(__name__)

EARNINGS_WINDOW_DAYS = 5         # fire when earnings are within this many calendar days
EXDIV_WINDOW_DAYS = 3


def _as_date(x) -> datetime | None:
    """yfinance.calendar returns a mix of datetime / pandas Timestamp /
    list[Timestamp] depending on version. Normalize to a plain datetime
    (or None if nothing usable)."""
    if x is None:
        return None
    if isinstance(x, list):
        x = x[0] if x else None
        if x is None:
            return None
    # pandas Timestamp has `to_pydatetime`; datetime passes through.
    to_py = getattr(x, "to_pydatetime", None)
    if callable(to_py):
        return to_py()
    if isinstance(x, datetime):
        return x
    # Last resort: try to parse ISO strings.
    try:
        return datetime.fromisoformat(str(x))
    except Exception:  # noqa: BLE001
        return None


def _calendar(ticker: str) -> dict | None:
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
    except Exception as e:  # noqa: BLE001
        logger.debug("yfinance.calendar(%s) failed: %s", ticker, e)
        return None
    # Older yfinance returns a DataFrame; newer returns a dict.
    if cal is None:
        return None
    if hasattr(cal, "to_dict"):
        try:
            return cal.to_dict()
        except Exception:  # noqa: BLE001
            return None
    if isinstance(cal, dict):
        return cal
    return None


def _earnings_proximity(ticker: str, as_of: str, cal: dict) -> list[Trigger]:
    # Keys yfinance has used across versions: "Earnings Date", "earningsDate",
    # also sometimes a tuple of (next_estimate, next_confirmed).
    raw = cal.get("Earnings Date") or cal.get("earningsDate") or cal.get("Earnings")
    dt = _as_date(raw)
    if dt is None:
        return []
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    days = (dt - as_of_dt).days
    if 0 <= days <= EARNINGS_WINDOW_DAYS:
        return [Trigger(
            ticker=ticker, as_of=as_of, kind="earnings_within_window",
            direction="neutral", magnitude=float(days),
            detail=f"Earnings in {days}d ({dt.date().isoformat()})",
            source="scheduled",
        )]
    return []


def _exdiv_proximity(ticker: str, as_of: str, cal: dict) -> list[Trigger]:
    raw = cal.get("Ex-Dividend Date") or cal.get("exDividendDate")
    dt = _as_date(raw)
    if dt is None:
        return []
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    days = (dt - as_of_dt).days
    if 0 <= days <= EXDIV_WINDOW_DAYS:
        return [Trigger(
            ticker=ticker, as_of=as_of, kind="exdiv_within_window",
            direction="neutral", magnitude=float(days),
            detail=f"Ex-dividend in {days}d ({dt.date().isoformat()})",
            source="scheduled",
        )]
    return []


def check(ticker: str, as_of: str) -> list[Trigger]:
    cal = _calendar(ticker)
    if not cal:
        return []
    out: list[Trigger] = []
    for fn in (_earnings_proximity, _exdiv_proximity):
        try:
            out.extend(fn(ticker, as_of, cal))
        except Exception as e:  # noqa: BLE001
            logger.debug("trigger.scheduled.%s(%s@%s) failed: %s", fn.__name__, ticker, as_of, e)
    return out
