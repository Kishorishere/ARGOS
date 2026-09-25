"""Structured fundamentals storage (P8).

Persists per-symbol per-quarter financial data pulled from ShareSansar
(when scraper wired) or manually entered. Schema targets Nepal
Commercial Banks + Insurance + Hydro since those are the meaningful
NEPSE universe; other sectors get partial fills.

Storage: `data/fundamentals/{SYMBOL}.jsonl` (one row per quarter).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FUND_DIR = Path(__file__).resolve().parents[2] / "data" / "fundamentals"


@dataclass
class QuarterlyReport:
    symbol: str
    quarter: str                    # "2024-Q3", "2024-Q4", ...
    fiscal_year_end: str            # NEPSE FY ends in Ashadh (~July 15)
    revenue_npr: float = 0.0        # for insurance/hydro: net premium / gross generation
    net_profit_npr: float = 0.0
    eps_npr: float = 0.0            # earnings per share
    book_value_npr: float = 0.0     # per share
    p_e_ratio: float = 0.0
    p_b_ratio: float = 0.0
    roe_pct: float = 0.0
    roa_pct: float = 0.0
    # Commercial-bank-specific
    npl_ratio_pct: float = 0.0      # non-performing loans
    cap_adequacy_pct: float = 0.0   # capital adequacy ratio (CAR)
    distributable_profit_npr: float = 0.0
    # Meta
    sector: str = "Unknown"
    source: str = "manual"
    fetched_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _path_for(symbol: str) -> Path:
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    return FUND_DIR / f"{symbol.upper()}.jsonl"


def load_reports(symbol: str) -> list[QuarterlyReport]:
    p = _path_for(symbol)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(QuarterlyReport(**json.loads(line)))
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping malformed fundamentals line for %s: %s", symbol, e)
    return out


def latest_quarter(symbol: str) -> Optional[QuarterlyReport]:
    reports = sorted(load_reports(symbol), key=lambda r: r.quarter)
    return reports[-1] if reports else None


def save_report(report: QuarterlyReport) -> None:
    """Append-only — dedupe on (symbol, quarter)."""
    p = _path_for(report.symbol)
    existing = load_reports(report.symbol)
    if any(r.quarter == report.quarter for r in existing):
        logger.info("Report already exists for %s %s — skipping",
                    report.symbol, report.quarter)
        return
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(report.to_dict()) + "\n")
    logger.info("Saved %s %s fundamentals", report.symbol, report.quarter)


def load_universe_latest() -> dict[str, QuarterlyReport]:
    """Return {symbol: latest_quarterly_report} for every symbol on file."""
    out: dict[str, QuarterlyReport] = {}
    if not FUND_DIR.exists():
        return out
    for p in FUND_DIR.glob("*.jsonl"):
        symbol = p.stem
        latest = latest_quarter(symbol)
        if latest:
            out[symbol] = latest
    return out


# ── Stub scraper ────────────────────────────────────────────────────────

def fetch_from_sharesansar(symbol: str) -> Optional[QuarterlyReport]:
    """Placeholder. Real implementation:

    1. GET https://www.sharesansar.com/company/{SYMBOL} to get session cookie
    2. Parse the "Financial Summary" table (quarterly rows)
    3. Extract revenue, net_profit, eps, roe, npl_ratio, etc.
    4. Also scrape /companies/{SYMBOL}/quarterly-financial-report for
       detailed line-items

    The ShareSansar company page IS accessible directly (HTTP 200 in dev),
    so this scraper is buildable — it's ~200 lines of BeautifulSoup and I
    can add it once we prioritize live-fetch over the current
    "populate manually" workflow.
    """
    logger.info("ShareSansar fundamentals scraper is a stub for %s. "
                "Populate data/fundamentals/%s.jsonl manually or wait for "
                "a proper scraper.", symbol, symbol.upper())
    return None
