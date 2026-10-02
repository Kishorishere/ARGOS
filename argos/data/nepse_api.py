"""Client for the user's NEPSE market-snapshot API.

Wraps https://nepse.nandisolutions.com/api/nepse/market (base configurable
via NEPSE_API_URL). The endpoint returns today's NEPSE market state — a
Merolagani-sourced snapshot with:
  - `summary` — NEPSE index, breadth, turnover
  - `index` — same but with OHLC of the index itself for the day
  - `topGainers` / `topLosers` — 10 items each (symbol, ltp, changePercent, volume, turnover)
  - `items` — ALL traded symbols today (~341) with OHLC + previousClose + volume + turnover
  - `marketStatus` — {isOpen, asOf}
  - `source`, `fetchedAt`, `responseTimeMs`

Rate-limited: response is cached by the upstream (X-Cache header); we honour
that by not spamming — one call per minute is more than sufficient for a
research desk.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://nepse.nandisolutions.com"
DEFAULT_TIMEOUT = 20

# In-process snapshot cache: the snapshot is expensive (~2.7s) and every
# ensemble source pulls from it. One forecast triggers 17 identical fetches
# without this. Cache lives for _SNAPSHOT_TTL seconds since the snapshot
# only changes tick-to-tick and this is a research desk, not an execution
# path. Set _SNAPSHOT_TTL to 0 to disable.
_SNAPSHOT_TTL = float(os.environ.get("NEPSE_SNAPSHOT_TTL_S", "300"))
_snapshot_cache: dict[str, Any] = {"data": None, "ts": 0.0}


def get_base_url() -> str:
    return os.environ.get("NEPSE_API_URL", DEFAULT_BASE).rstrip("/")


def fetch_market_snapshot(refresh: bool = False,
                          timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch the full NEPSE market snapshot.

    Args:
        refresh: append ?refresh=true to force the upstream to skip its cache.
                 Also bypasses our local in-process cache.
        timeout: HTTP timeout seconds.
    """
    now = time.monotonic()
    if (not refresh and _SNAPSHOT_TTL > 0
            and _snapshot_cache["data"] is not None
            and now - _snapshot_cache["ts"] < _SNAPSHOT_TTL):
        return _snapshot_cache["data"]

    url = f"{get_base_url()}/api/nepse/market"
    if refresh:
        url += "?refresh=true"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"NEPSE API returned success=false: {data}")
    _snapshot_cache["data"] = data
    _snapshot_cache["ts"] = now
    return data


def clear_snapshot_cache() -> None:
    """Wipe the in-process snapshot cache. Tests + backtests only."""
    _snapshot_cache["data"] = None
    _snapshot_cache["ts"] = 0.0


def _index(items_by_symbol: dict, symbol: str) -> dict | None:
    return items_by_symbol.get(symbol.upper())


def as_of_today(symbols: list[str] | None = None) -> dict[str, dict]:
    """Return {symbol: item} for today's traded symbols.

    Each `item` has open/high/low/ltp/previousClose/volume/turnover.
    Filter to `symbols` when given.
    """
    snap = fetch_market_snapshot()
    items = snap.get("items") or []
    out: dict[str, dict] = {}
    for it in items:
        sym = str(it.get("symbol", "")).upper()
        if not sym:
            continue
        if symbols and sym not in {s.upper() for s in symbols}:
            continue
        out[sym] = it
    return out


def market_breadth() -> dict[str, Any]:
    """Compact breadth view — gainers/losers count, index change, turnover."""
    snap = fetch_market_snapshot()
    items = snap.get("items") or []
    advances = sum(1 for it in items if (it.get("change") or 0) > 0)
    declines = sum(1 for it in items if (it.get("change") or 0) < 0)
    unchanged = sum(1 for it in items if (it.get("change") or 0) == 0)
    summary = snap.get("summary") or {}
    return {
        "as_of": snap.get("fetchedAt"),
        "market_open": (snap.get("marketStatus") or {}).get("isOpen"),
        "nepse_index": summary.get("nepseIndex"),
        "nepse_change_pct": summary.get("indexChangePercent"),
        "total_turnover_npr": summary.get("totalTurnover"),
        "total_transactions": summary.get("totalTransactions"),
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_decline_ratio": round(advances / max(declines, 1), 3),
        "top_gainers": [g.get("symbol") for g in (snap.get("topGainers") or [])[:5]],
        "top_losers": [l.get("symbol") for l in (snap.get("topLosers") or [])[:5]],
    }


# ── Rich data endpoints (added after API discovery) ─────────────────────

def fetch_companies(timeout: int = 20) -> list[dict]:
    """649 companies with id, symbol, name, sector, status, instrumentType."""
    url = f"{get_base_url()}/api/nepse/companies"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return (resp.json() or {}).get("data") or []


def fetch_floorsheet(page: int = 0, timeout: int = 30) -> dict:
    """Paginated floorsheet with buyer/seller broker + qty + price + date."""
    url = f"{get_base_url()}/api/nepse/floorsheet"
    params = {"page": page} if page else None
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return (resp.json() or {}).get("data") or {}


def fetch_dividends(timeout: int = 20) -> list[dict]:
    """Recent dividend announcements: symbol, cash%, bonus%, book_close, fiscal_year."""
    url = f"{get_base_url()}/api/nepse/dividends"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return ((resp.json() or {}).get("data") or {}).get("dividends") or []


def fetch_announcements(timeout: int = 20) -> list[dict]:
    """Corporate announcements: title, summary, date, symbol, category."""
    url = f"{get_base_url()}/api/nepse/announcements"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return ((resp.json() or {}).get("data") or {}).get("announcements") or []


def fetch_indices(timeout: int = 20) -> list[dict]:
    """13 indices with today's OHLC + change/turnover/volume."""
    url = f"{get_base_url()}/api/nepse/indices"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return (resp.json() or {}).get("data") or []


# ── Cached derived views ────────────────────────────────────────────────

_COMPANIES_CACHE: dict[str, dict] | None = None


def companies_by_symbol() -> dict[str, dict]:
    """Cached {symbol: company_meta} — replaces our hardcoded SECTOR_MAP.

    Falls back to empty dict on network failure so calling code can
    gracefully degrade to the hardcoded map.
    """
    global _COMPANIES_CACHE
    if _COMPANIES_CACHE is not None:
        return _COMPANIES_CACHE
    try:
        _COMPANIES_CACHE = {c["symbol"].upper(): c for c in fetch_companies() if c.get("symbol")}
    except Exception as e:  # noqa: BLE001
        logger.warning("companies_by_symbol fetch failed: %s", e)
        _COMPANIES_CACHE = {}
    return _COMPANIES_CACHE
