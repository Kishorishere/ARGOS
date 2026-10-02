"""Unified event registry.

Normalizes four heterogeneous NEPSE event sources into a single
`EventRecord` shape so the event-conditional classifier
(`argos.learn.event_conditional`) can train on them uniformly.

Sources in priority order (best-tagged / most-transparent first):
  * ``sebon_promoter``   — argos.data.sebon_disclosures (item 1)
  * ``dividend``         — bullish payout-adjacent titles in argos.data.announcements
  * ``earnings``         — quarterly-report-adjacent titles in argos.data.announcements
  * ``broker_cluster``   — argos.data.broker_patterns.top_buyer_streak with >=3 days

Every EventRecord carries a ``magnitude`` scalar so the model can weight
"big dividend" vs "small dividend" without introducing separate labels.
Magnitude is source-specific and normalized to a reasonable numeric range
(documented per source).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
ANN_DIR = REPO / "data" / "announcements"
SEBON_DIR = ANN_DIR / "sebon_promoter"


EventType = Literal["dividend", "earnings", "broker_cluster", "sebon_promoter"]


@dataclass(frozen=True)
class EventRecord:
    symbol: str
    event_date: str          # YYYY-MM-DD
    event_type: EventType
    magnitude: float         # source-specific; see extractor docstrings
    direction_hint: int      # +1 bullish, -1 bearish, 0 unknown
    source_id: str           # opaque identifier for dedup / provenance
    raw: dict                # source dict (short, for debugging)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Keyword filters (compiled once) ────────────────────────────────────────
_DIV_TERMS = re.compile(
    r"\b(dividend|bonus|right.?share|book.?closure|record.?date|"
    r"cash.?dividend|stock.?dividend)\b",
    re.IGNORECASE,
)
_EARN_TERMS = re.compile(
    r"\b(quarterly|q1|q2|q3|q4|first.?quarter|second.?quarter|"
    r"third.?quarter|fourth.?quarter|annual.?report|financial.?statement|"
    r"unaudited|audited|earnings)\b",
    re.IGNORECASE,
)
_PCT_IN_TITLE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _read_ann_files(since: date | None = None) -> list[dict]:
    if not ANN_DIR.exists():
        return []
    out: list[dict] = []
    for jf in sorted(ANN_DIR.glob("*.jsonl")):
        try:
            file_date = datetime.strptime(jf.stem, "%Y-%m-%d").date()
        except ValueError:
            continue    # skip score_by_symbol.json and any non-date file
        if since and file_date < since:
            continue
        for line in jf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                d["_file_date"] = file_date.isoformat()
                out.append(d)
            except Exception:
                continue
    return out


def _dividend_events(anns: list[dict], symbol: str | None) -> list[EventRecord]:
    out: list[EventRecord] = []
    for a in anns:
        sym = str(a.get("symbol", "")).upper()
        if not sym:
            continue
        if symbol and sym != symbol.upper():
            continue
        text = f"{a.get('title', '')} {a.get('summary', '')}"
        if not _DIV_TERMS.search(text):
            continue
        # Magnitude: parse first "%" number from title. Cash-dividend
        # yield is what matters; default 0 if not embedded.
        m = _PCT_IN_TITLE.search(text)
        magnitude = float(m.group(1)) if m else 0.0
        event_date = a.get("dateAD") or a.get("_file_date")
        out.append(EventRecord(
            symbol=sym, event_date=event_date,
            event_type="dividend", magnitude=magnitude,
            direction_hint=+1,       # dividends generally bullish short-term
            source_id=str(a.get("id") or f"{sym}-{event_date}-div"),
            raw={"title": a.get("title"), "category": a.get("category")},
        ))
    return out


def _earnings_events(anns: list[dict], symbol: str | None) -> list[EventRecord]:
    out: list[EventRecord] = []
    for a in anns:
        sym = str(a.get("symbol", "")).upper()
        if not sym:
            continue
        if symbol and sym != symbol.upper():
            continue
        text = f"{a.get('title', '')} {a.get('summary', '')}"
        # Exclude mutual-fund NAV posts (not real earnings)
        if a.get("category") in ("financialDocument", "mutualFund") and not _EARN_TERMS.search(text):
            continue
        if not _EARN_TERMS.search(text):
            continue
        event_date = a.get("dateAD") or a.get("_file_date")
        out.append(EventRecord(
            symbol=sym, event_date=event_date,
            event_type="earnings", magnitude=0.0,     # SUE unavailable without join
            direction_hint=0,
            source_id=str(a.get("id") or f"{sym}-{event_date}-earn"),
            raw={"title": a.get("title"), "category": a.get("category")},
        ))
    return out


def _sebon_events(symbol: str | None) -> list[EventRecord]:
    if not SEBON_DIR.exists():
        return []
    out: list[EventRecord] = []
    for jf in sorted(SEBON_DIR.glob("*.jsonl")):
        for line in jf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            sym = (d.get("symbol_hint") or "").upper()
            if not sym:
                continue    # skip untagged rows — noise, not signal
            if symbol and sym != symbol.upper():
                continue
            event_date = d.get("notice_date") or jf.stem
            out.append(EventRecord(
                symbol=sym, event_date=event_date,
                event_type="sebon_promoter", magnitude=1.0,
                direction_hint=-1,    # promoter selling ~= bearish prior
                source_id=d.get("url") or f"{sym}-{event_date}-sebon",
                raw={"title": d.get("title"), "url": d.get("url")},
            ))
    return out


def _broker_cluster_events(symbol: str, since: date) -> list[EventRecord]:
    """Fire an event on days where >=3 distinct top-buyer brokers hit the
    same symbol within a 5-bar rolling window. Uses the existing
    `argos.data.broker_patterns` primitives.

    Note: this is O(days × 5) floorsheet loads. Cheap because the
    per-day loads are lru-cached inside broker_patterns.
    """
    try:
        from argos.data.broker_patterns import top_buyer_streak
    except Exception:
        return []

    out: list[EventRecord] = []
    today = date.today()
    d = since
    seen_windows: set[str] = set()
    while d <= today:
        as_of = d.isoformat()
        try:
            info = top_buyer_streak(symbol, as_of, days=5, min_days_top=3)
        except Exception:
            info = None
        if info and info.get("distinct_top_brokers", 0) >= 3:
            # Dedupe overlapping windows — one event per streak-end date
            key = f"{symbol}-{as_of}-{info.get('distinct_top_brokers')}"
            if key not in seen_windows:
                seen_windows.add(key)
                out.append(EventRecord(
                    symbol=symbol.upper(), event_date=as_of,
                    event_type="broker_cluster",
                    magnitude=float(info.get("distinct_top_brokers") or 0),
                    direction_hint=+1,
                    source_id=key,
                    raw={"top_brokers": info.get("distinct_top_brokers"),
                         "streak_days": info.get("streak_days")},
                ))
        d += timedelta(days=1)
    return out


# ── Public API ─────────────────────────────────────────────────────────────

def load_events(symbol: str | None = None,
                since_date: str | None = None,
                include_broker_cluster: bool = False,
                sources: list[EventType] | None = None,
                ) -> list[EventRecord]:
    """Return all EventRecords for `symbol` (or the whole universe) on or
    after `since_date` (YYYY-MM-DD), sorted by event_date.

    `include_broker_cluster` defaults to False because it walks the
    floorsheet per day and is only useful when a specific symbol is
    named (returns [] when symbol is None to keep costs bounded).

    `sources` filters to a subset when set.
    """
    since = datetime.strptime(since_date, "%Y-%m-%d").date() if since_date else None
    anns = _read_ann_files(since=since)

    wanted = set(sources) if sources else {"dividend", "earnings",
                                            "sebon_promoter", "broker_cluster"}

    out: list[EventRecord] = []
    if "dividend" in wanted:
        out.extend(_dividend_events(anns, symbol))
    if "earnings" in wanted:
        out.extend(_earnings_events(anns, symbol))
    if "sebon_promoter" in wanted:
        for r in _sebon_events(symbol):
            if since and r.event_date and r.event_date < since_date:
                continue
            out.append(r)
    if include_broker_cluster and symbol and "broker_cluster" in wanted:
        walk_since = since or (date.today() - timedelta(days=365))
        out.extend(_broker_cluster_events(symbol, walk_since))

    # Dedup by (symbol, event_date, event_type, source_id)
    seen = set()
    uniq: list[EventRecord] = []
    for r in out:
        key = (r.symbol, r.event_date, r.event_type, r.source_id)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    uniq.sort(key=lambda r: (r.event_date or "", r.symbol, r.event_type))
    return uniq


def events_in_last(symbol: str, as_of_date: str, n_bars: int,
                   include_broker_cluster: bool = True) -> list[EventRecord]:
    """Return events for `symbol` that fired within the last `n_bars`
    CALENDAR days before `as_of_date` (inclusive). Uses calendar days
    for simplicity — trading-day mapping happens at scoring time.
    """
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    since = as_of - timedelta(days=n_bars)
    rows = load_events(symbol=symbol, since_date=since.isoformat(),
                       include_broker_cluster=include_broker_cluster)
    return [r for r in rows if r.event_date and since.isoformat()
            <= r.event_date <= as_of_date]
