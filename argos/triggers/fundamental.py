"""Fundamental triggers — insider activity from EDGAR Form 4.

Phase C upgrade from the Phase B stub. One rule:

  insider_form4_recent — a non-derivative insider transaction landed in
    the last N calendar days with either (a) size > USD_THRESHOLD or
    (b) count of transactions > COUNT_THRESHOLD in the window. Direction
    is `bullish` on acquire, `bearish` on dispose.

The 13F cluster trigger stays deferred — see Phase C.2 note in
argos/data/edgar.py::top_institutional_holders.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from argos.triggers.base import Trigger

logger = logging.getLogger(__name__)

WINDOW_DAYS = 5
USD_THRESHOLD = 1_000_000.0
COUNT_THRESHOLD = 3


def _usd_value(event) -> float:
    s = event.transaction_shares or 0
    p = event.transaction_price or 0
    return float(s) * float(p)


def check(ticker: str, as_of: str) -> list[Trigger]:
    try:
        from argos.data.edgar import recent_form4s
        events = recent_form4s(ticker, limit=50, max_fetch=25)
    except Exception as e:  # noqa: BLE001
        logger.debug("fundamental trigger: edgar fetch failed on %s: %s", ticker, e)
        return []

    if not events:
        return []

    try:
        as_of_dt = datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        return []
    window_start = as_of_dt - timedelta(days=WINDOW_DAYS)

    acquires = []
    disposes = []
    for e in events:
        d = e.transaction_date or e.filed_at
        if not d:
            continue
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if dd < window_start or dd > as_of_dt:
            continue
        if e.direction == "A":
            acquires.append(e)
        elif e.direction == "D":
            disposes.append(e)

    triggers: list[Trigger] = []

    def _maybe(group: list, direction: str, label: str) -> None:
        if not group:
            return
        usd = sum(_usd_value(x) for x in group)
        count = len(group)
        if usd < USD_THRESHOLD and count < COUNT_THRESHOLD:
            return
        top = max(group, key=_usd_value)
        detail = (
            f"{count} insider {label}(s) worth ${usd:,.0f} in last {WINDOW_DAYS}d; "
            f"largest: {top.reporting_owner or '?'} ({top.owner_title or '?'}) "
            f"{top.transaction_shares or 0:,.0f}sh"
        )
        triggers.append(Trigger(
            ticker=ticker.upper(), as_of=as_of,
            kind=f"insider_form4_{label}",
            direction=direction, magnitude=float(usd),
            detail=detail, source="fundamental",
        ))

    _maybe(acquires, "bullish", "buy")
    _maybe(disposes, "bearish", "sell")
    return triggers
