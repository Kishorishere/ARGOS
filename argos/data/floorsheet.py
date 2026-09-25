"""NEPSE floorsheet — broker-level trade flow (P9).

Purpose:
- Detect PERSISTENT accumulation by big brokers (a broker that's been the
  top-buyer of ADBL for >5 consecutive sessions is a real signal).
- Track top-3 broker concentration on the buy vs sell side.
- Smart-money score in [-1, +1] fed into the ensemble.

Data source:
- Primary: nepalstock.com.np `/floor-sheet` (JSON API when session cookie is set)
- Fallback: sharesansar `/floor-sheet` HTML table
- Stub: return empty result set with clear message

Storage:
    data/floorsheet/{YYYY-MM-DD}.parquet  — per-day snapshot
    data/floorsheet_scores/{SYMBOL}.jsonl — per-symbol rolling scores

Because floorsheet scraping requires session flow (both sources) that
we haven't fully wired yet, this module ships as a functional STUB that:
  - defines the schema + storage
  - implements score aggregation
  - exposes `smart_money_score(symbol)` for the ensemble
  - accepts manual data loads via `ingest_floorsheet_rows()`
When accumulated data exists, everything works. When not, `smart_money_score`
returns 0.0 (neutral, no ensemble contribution).
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

FLOORSHEET_DIR = Path(__file__).resolve().parents[2] / "data" / "floorsheet"
SCORES_DIR = Path(__file__).resolve().parents[2] / "data" / "floorsheet_scores"


@dataclass
class FloorsheetRow:
    """One trade record from the daily floorsheet."""
    date: str
    symbol: str
    buyer_broker_id: str        # broker number, e.g., "7" or "45"
    seller_broker_id: str
    quantity: int
    price: float
    amount: float               # quantity * price


def _path_for(d: str) -> Path:
    FLOORSHEET_DIR.mkdir(parents=True, exist_ok=True)
    return FLOORSHEET_DIR / f"{d}.jsonl"


def ingest_floorsheet_rows(rows: list[FloorsheetRow]) -> None:
    """Persist a day's floorsheet rows. Idempotent per-day."""
    if not rows:
        return
    by_date: dict[str, list[FloorsheetRow]] = defaultdict(list)
    for r in rows:
        by_date[r.date].append(r)
    for d, day_rows in by_date.items():
        p = _path_for(d)
        with open(p, "w", encoding="utf-8") as f:
            for r in day_rows:
                f.write(json.dumps(r.__dict__) + "\n")
        logger.info("Floorsheet: wrote %d rows for %s", len(day_rows), d)


def _load_day(d: str) -> list[FloorsheetRow]:
    p = _path_for(d)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(FloorsheetRow(**json.loads(line)))
            except Exception:
                continue
    return out


def daily_symbol_scores(d: str, symbol: str) -> dict:
    """For one symbol on one day, compute:
      - top-3 buyer concentration % of volume
      - top-3 seller concentration
      - top buyer broker id + volume
      - top seller broker id + volume
      - total volume
    """
    rows = [r for r in _load_day(d) if r.symbol.upper() == symbol.upper()]
    if not rows:
        return {}
    total_vol = sum(r.quantity for r in rows)
    buyers = Counter()
    sellers = Counter()
    for r in rows:
        buyers[r.buyer_broker_id] += r.quantity
        sellers[r.seller_broker_id] += r.quantity
    top_buyers = buyers.most_common(3)
    top_sellers = sellers.most_common(3)
    return {
        "date": d,
        "symbol": symbol.upper(),
        "total_volume": total_vol,
        "top_3_buyer_conc_pct": round(100 * sum(v for _, v in top_buyers) / max(total_vol, 1), 2),
        "top_3_seller_conc_pct": round(100 * sum(v for _, v in top_sellers) / max(total_vol, 1), 2),
        "top_buyer": {"broker": top_buyers[0][0], "volume": top_buyers[0][1]} if top_buyers else None,
        "top_seller": {"broker": top_sellers[0][0], "volume": top_sellers[0][1]} if top_sellers else None,
        "n_trades": len(rows),
    }


def smart_money_score(symbol: str, as_of_date: str, lookback_days: int = 10) -> float:
    """Aggregate smart-money score in [-1, +1] over the last N days.

    Positive: same big broker keeps accumulating (persistent buyer);
              top-3 buyers concentrating (institutional flow in).
    Negative: same big broker keeps distributing; top-3 sellers concentrating.

    Returns 0.0 if we don't have floorsheet data (stub behavior — no
    ensemble contribution when data is missing).
    """
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    days = [(as_of - timedelta(days=i)).isoformat() for i in range(lookback_days)]
    daily_scores = []
    top_buyers_seq = []
    top_sellers_seq = []
    for d in days:
        s = daily_symbol_scores(d, symbol)
        if not s:
            continue
        daily_scores.append(s)
        if s.get("top_buyer"):
            top_buyers_seq.append(s["top_buyer"]["broker"])
        if s.get("top_seller"):
            top_sellers_seq.append(s["top_seller"]["broker"])
    if not daily_scores:
        return 0.0

    # Signal 1: buyer concentration minus seller concentration (normalized)
    buy_conc = sum(s["top_3_buyer_conc_pct"] for s in daily_scores) / len(daily_scores)
    sell_conc = sum(s["top_3_seller_conc_pct"] for s in daily_scores) / len(daily_scores)
    concentration_signal = (buy_conc - sell_conc) / 100.0  # [-1..+1]

    # Signal 2: persistent top-buyer (same broker in top-1 5+ consecutive days = bullish)
    persistent_buyer = _persistent_top(top_buyers_seq)
    persistent_seller = _persistent_top(top_sellers_seq)
    persistence_signal = 0.5 * (persistent_buyer - persistent_seller)

    score = max(-1.0, min(1.0, concentration_signal + persistence_signal))
    return round(score, 3)


def _persistent_top(seq: list[str], min_run: int = 5) -> float:
    """Return 1.0 if the same broker was #1 for min_run+ recent sessions, else 0."""
    if len(seq) < min_run:
        return 0.0
    recent = seq[:min_run]  # most recent N (seq is newest-first)
    return 1.0 if len(set(recent)) == 1 else 0.0


# ── Stub scraper ────────────────────────────────────────────────────────

def fetch_floorsheet_from_nepse_api(max_pages: int = 300,
                                    progress_every: int = 20) -> list[FloorsheetRow]:
    """Pull TODAY's full floorsheet from nepse.nandisolutions.com's paginated
    endpoint (~500 rows per page).

    Iterates until `last=True`. Default cap 300 pages (~150k rows) covers a
    typical daily volume (~127k trades). Prints a progress line every
    `progress_every` pages so long fetches aren't silent.
    """
    from argos.data.nepse_api import fetch_floorsheet
    rows: list[FloorsheetRow] = []
    total_pages_expected = None
    last_page = -1
    for page in range(max_pages):
        try:
            data = fetch_floorsheet(page=page)
        except Exception as e:  # noqa: BLE001
            logger.warning("Floorsheet page %d fetch failed: %s", page, e)
            break
        fs_wrapper = data.get("floorsheets") or {}
        page_rows = fs_wrapper.get("content") or []
        if total_pages_expected is None:
            total_pages_expected = fs_wrapper.get("totalPages", "?")
        for r in page_rows:
            try:
                rows.append(FloorsheetRow(
                    date=r["businessDate"],
                    symbol=r["stockSymbol"],
                    buyer_broker_id=str(r.get("buyerMemberId", "")),
                    seller_broker_id=str(r.get("sellerMemberId", "")),
                    quantity=int(r.get("contractQuantity", 0)),
                    price=float(r.get("contractRate", 0.0)),
                    amount=float(r.get("contractAmount", 0.0)),
                ))
            except Exception:
                continue
        last_page = page
        if progress_every and (page + 1) % progress_every == 0:
            logger.info("Floorsheet page %d/%s → %d rows so far",
                        page + 1, total_pages_expected, len(rows))
        if fs_wrapper.get("last"):
            break
    logger.info("Floorsheet: fetched %d rows across %d pages (of %s expected)",
                len(rows), last_page + 1, total_pages_expected)
    return rows


# Preserve old name so other imports still work
fetch_floorsheet_from_nepalstock = fetch_floorsheet_from_nepse_api
