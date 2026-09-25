"""NEPSE sector indices + per-symbol sector mapping (P4).

Two functions:
  1. `fetch_sectors()` — hit https://nepse.nandisolutions.com/api/nepse/sectors
     for today's per-sector index + change%
  2. `symbol_to_sector(SYM)` — hardcoded mapping for top-liquidity NEPSE
     symbols (covers ~90% of typical daily turnover)

Also:
  - `snapshot_sectors_today()` appends today's snapshot to
    data/sector_history.jsonl so relative-strength history accumulates
    over time (weekly / monthly RS becomes meaningful after ~30 daily runs)
  - `relative_strength(symbol, as_of, window)` returns the sector's
    return over the last N days minus NEPSE index return
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SECTOR_HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "sector_history.jsonl"
DEFAULT_BASE = os.environ.get("NEPSE_API_URL", "https://nepse.nandisolutions.com").rstrip("/")


# ── Symbol → sector mapping ────────────────────────────────────────────
# Hardcoded for high-liquidity NEPSE names. Fallback: infer from suffix.
# Sector names match the /api/nepse/sectors "name" field exactly so the
# fetch-side lookup works.
SECTOR_MAP: dict[str, str] = {
    # Commercial banks (Banking SubIndex)
    "NABIL": "Banking SubIndex", "ADBL": "Banking SubIndex",
    "EBL":   "Banking SubIndex", "GBIME": "Banking SubIndex",
    "NICA":  "Banking SubIndex", "HBL":   "Banking SubIndex",
    "SANIMA":"Banking SubIndex", "MBL":   "Banking SubIndex",
    "CZBIL": "Banking SubIndex", "PRVU":  "Banking SubIndex",
    "SBI":   "Banking SubIndex", "SCB":   "Banking SubIndex",
    "LSL":   "Banking SubIndex", "KBL":   "Banking SubIndex",
    "NMB":   "Banking SubIndex", "SBL":   "Banking SubIndex",
    "GLBSL": "Banking SubIndex", "PCBL":  "Banking SubIndex",
    "RBCL":  "Banking SubIndex",
    # Hydropower
    "UPPER":  "HydroPower Index", "HDL":    "HydroPower Index",
    "NHPC":   "HydroPower Index", "KKHC":   "HydroPower Index",
    "RURU":   "HydroPower Index", "AKPL":   "HydroPower Index",
    "API":    "HydroPower Index", "UMHL":   "HydroPower Index",
    "SHPC":   "HydroPower Index", "BARUN":  "HydroPower Index",
    "BPCL":   "HydroPower Index", "CHCL":   "HydroPower Index",
    "MHNL":   "HydroPower Index", "NHDL":   "HydroPower Index",
    "SJCL":   "HydroPower Index", "TPC":    "HydroPower Index",
    # Life insurance
    "NLIC":   "Life Insurance", "LICN":   "Life Insurance",
    "ALICL":  "Life Insurance", "GLICL":  "Life Insurance",
    "PLIC":   "Life Insurance", "SLICL":  "Life Insurance",
    "RLI":    "Life Insurance", "SLI":    "Life Insurance",
    # Non-life insurance
    "NIL":    "Non Life Insurance", "PIC":    "Non Life Insurance",
    "HGI":    "Non Life Insurance", "NICL":   "Non Life Insurance",
    "SIL":    "Non Life Insurance", "NIC":    "Non Life Insurance",
    "SICL":   "Non Life Insurance", "UIC":    "Non Life Insurance",
    # Finance
    "GFCL":   "Finance Index", "PROFL":  "Finance Index",
    "CFCL":   "Finance Index", "GUFL":   "Finance Index",
    # Development Bank
    "NUBL":   "Development Bank Index", "MDB":    "Development Bank Index",
    "SADBL":  "Development Bank Index",
    # Microfinance
    "ILBS":   "Microfinance Index", "FMDBL":  "Microfinance Index",
    "SKBBL":  "Microfinance Index", "CBBL":   "Microfinance Index",
    # Hotels
    "TRH":    "Hotels And Tourism Index", "OHL":    "Hotels And Tourism Index",
    "YHL":    "Hotels And Tourism Index",
    # Infrastructure / mutual funds
    "NIFRA":  "Others Index", "HIDCLP": "Others Index",
    "NRIC":   "Others Index", "NRN":    "Others Index",
    # Manufacturing
    "BNL":    "Manufacturing And Processing", "UNL":    "Manufacturing And Processing",
    "SHIVM":  "Manufacturing And Processing",
    # Trading
    "STC":    "Trading Index", "BBC":    "Trading Index",
}


def symbol_to_sector(symbol: str) -> str | None:
    """Best-effort sector lookup — prefer the live NEPSE-API companies feed,
    fall back to the hardcoded SECTOR_MAP.

    Maps API sector names to the sector-index names used by
    `today_sector_scores()` (e.g. "Commercial Banks" → "Banking SubIndex").
    """
    sym = symbol.upper()
    try:
        from argos.data.nepse_api import companies_by_symbol
        rec = companies_by_symbol().get(sym)
        if rec and rec.get("sector"):
            return _API_TO_INDEX_NAME.get(rec["sector"], rec["sector"])
    except Exception:
        pass
    return SECTOR_MAP.get(sym)


# Map company sector names (from /api/nepse/companies) to sector-index names
# (from /api/nepse/sectors). Empirically observed mappings.
_API_TO_INDEX_NAME: dict[str, str] = {
    "Commercial Banks":          "Banking SubIndex",
    "Development Banks":         "Development Bank Index",
    "Finance":                    "Finance Index",
    "Microfinance":               "Microfinance Index",
    "Non Life Insurance":         "Non Life Insurance",
    "Life Insurance":             "Life Insurance",
    "Hydro Power":                "HydroPower Index",
    "Hotels":                     "Hotels And Tourism Index",
    "Hotels and Tourism":         "Hotels And Tourism Index",
    "Manufacturing And Processing":"Manufacturing And Processing",
    "Manufacturing and Processing":"Manufacturing And Processing",
    "Others":                     "Others Index",
    "Trading":                    "Trading Index",
    "Investment":                 "Investment Index",
    "Mutual Fund":                "Mutual Fund",
}


def fetch_sectors(timeout: int = 15) -> list[dict]:
    """Return today's sector indices from the NEPSE snapshot API."""
    r = requests.get(f"{DEFAULT_BASE}/api/nepse/sectors", timeout=timeout)
    r.raise_for_status()
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(f"nepse sectors API returned success=false: {d}")
    return d.get("data", [])


def snapshot_sectors_today() -> dict:
    """Append today's sector snapshot to data/sector_history.jsonl.

    Idempotent — safe to call multiple times per day; only writes if the
    latest snapshot's fetchedAt differs from what's already on disk.
    Returns the snapshot for immediate use.
    """
    SECTOR_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    sectors = fetch_sectors()
    today = date.today().isoformat()
    entry = {
        "date": today,
        "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "sectors": [{"name": s["name"], "index": s.get("index"),
                     "change_pct": s.get("changePercent", 0)}
                    for s in sectors],
    }
    # Check if today's row already exists
    if SECTOR_HISTORY_PATH.exists():
        for line in SECTOR_HISTORY_PATH.read_text().splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("date") == today:
                    return entry
            except Exception:
                continue
    with open(SECTOR_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    logger.info("Sector snapshot for %s appended (%d sectors)", today, len(sectors))
    return entry


def sector_relative_strength(sector_name: str,
                             window: int = 20) -> dict | None:
    """Sector index return over last `window` days minus NEPSE index return.

    Requires accumulated history in sector_history.jsonl (populated by
    snapshot_sectors_today). Returns None if not enough history.
    """
    if not SECTOR_HISTORY_PATH.exists():
        return None
    entries = []
    for line in SECTOR_HISTORY_PATH.read_text().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    entries.sort(key=lambda e: e["date"])
    if len(entries) < window + 1:
        return None
    lookback = entries[-window - 1:-1]
    latest = entries[-1]
    # Sector price return
    def _index(entry, name):
        for s in entry.get("sectors", []):
            if s["name"] == name:
                return s.get("index")
        return None
    start_s = _index(lookback[0], sector_name)
    end_s = _index(latest, sector_name)
    start_n = _index(lookback[0], "NEPSE Index")
    end_n = _index(latest, "NEPSE Index")
    if not (start_s and end_s and start_n and end_n):
        return None
    sector_ret = (end_s / start_s - 1) * 100
    nepse_ret = (end_n / start_n - 1) * 100
    return {
        "sector": sector_name,
        "window_days": window,
        "sector_return_pct": round(sector_ret, 3),
        "nepse_return_pct": round(nepse_ret, 3),
        "relative_strength_pct": round(sector_ret - nepse_ret, 3),
    }


def symbols_in_sector(sector_name: str) -> list[str]:
    """All known symbols whose sector matches `sector_name`.

    Prefers the live NEPSE-API companies feed; falls back to the hardcoded
    SECTOR_MAP. Sector-index-name aliases (e.g. "Commercial Banks" vs
    "Banking SubIndex") are handled via _API_TO_INDEX_NAME.
    """
    target = sector_name
    out: set[str] = set()
    try:
        from argos.data.nepse_api import companies_by_symbol
        for sym, rec in companies_by_symbol().items():
            raw = rec.get("sector") or ""
            mapped = _API_TO_INDEX_NAME.get(raw, raw)
            if raw == target or mapped == target:
                out.add(sym.upper())
    except Exception:
        pass
    for sym, sec in SECTOR_MAP.items():
        if sec == target:
            out.add(sym.upper())
    return sorted(out)


def today_sector_scores() -> dict[str, float]:
    """Simple today-only sector momentum: change% - NEPSE change%.

    Works with a single API call (no history required). Used as a
    fallback when sector_relative_strength() has no accumulated history.
    """
    sectors = fetch_sectors()
    nepse = next((s for s in sectors if s["name"] == "NEPSE Index"), None)
    nepse_chg = (nepse or {}).get("changePercent", 0) or 0
    return {
        s["name"]: round((s.get("changePercent", 0) or 0) - nepse_chg, 3)
        for s in sectors
    }
