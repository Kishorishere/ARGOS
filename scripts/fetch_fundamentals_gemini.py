"""Populate fundamentals for a list of NEPSE symbols via Gemini grounded search.

Instead of scraping ShareSansar's protected AJAX, we ask Gemini to fetch
the latest quarterly report and return structured JSON. Gemini's
Google-Search grounding pulls the real ShareSansar / Merolagani data
without any scraping code.

Usage: python scripts/fetch_fundamentals_gemini.py NABIL ADBL EBL ...
       python scripts/fetch_fundamentals_gemini.py --top10
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(r"C:\Users\Dell\Desktop\fuse\A-Multi-Agent-Trading-Research-Desk")
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env", override=True)

from argos.fundamentals import QuarterlyReport, save_report
from argos.model_router import _gemini_cfg, _make_llm_from_cfg
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("fetch_fund")


TOP_10 = ["NABIL", "ADBL", "EBL", "GBIME", "NICA", "HBL", "SANIMA", "UPPER", "HDL", "NIFRA"]


class FundamentalsExtract(BaseModel):
    """Schema Gemini fills for each ticker."""
    symbol: str
    quarter: str = Field(description="e.g., '2024-Q3' or '2024-Q4'")
    fiscal_year_end: str = Field(description="YYYY-MM-DD of the FY end")
    revenue_npr: float = 0.0
    net_profit_npr: float = 0.0
    eps_npr: float = 0.0
    book_value_npr: float = 0.0
    p_e_ratio: float = 0.0
    p_b_ratio: float = 0.0
    roe_pct: float = 0.0
    roa_pct: float = 0.0
    npl_ratio_pct: float = 0.0
    cap_adequacy_pct: float = 0.0
    distributable_profit_npr: float = 0.0
    sector: str = Field(description="Commercial Banks / HydroPower / Life Insurance / etc.")


def fetch_one(symbol: str) -> QuarterlyReport | None:
    """Ask Gemini to fetch + structure the latest quarterly for `symbol`."""
    logger.info("Fetching fundamentals for %s...", symbol)
    try:
        llm = _make_llm_from_cfg(_gemini_cfg(grounded=True))
        structured = llm.with_structured_output(FundamentalsExtract)
        prompt = (
            f"Fetch the LATEST published quarterly financial report for NEPSE ticker "
            f"'{symbol}' from ShareSansar (sharesansar.com/company/{symbol}) or Merolagani "
            f"(merolagani.com/CompanyDetail.aspx?symbol={symbol}). "
            f"Extract these fields as of that quarter:\n"
            f"- revenue (in NPR)\n"
            f"- net_profit (NPR)\n"
            f"- eps (NPR per share)\n"
            f"- book_value per share (NPR)\n"
            f"- P/E ratio\n"
            f"- P/B ratio\n"
            f"- return on equity (%)\n"
            f"- return on assets (%)\n"
            f"- NPL ratio for banks (%)\n"
            f"- capital adequacy (%) for banks\n"
            f"- distributable profit (NPR)\n"
            f"- sector name\n"
            f"- the quarter label like '2024-Q3'\n"
            f"- fiscal year end date (Nepali fiscal year ends mid-July, use ~2024-07-15)\n\n"
            f"Return ONLY the JSON object. If a field is unavailable, use 0."
        )
        result: FundamentalsExtract = structured.invoke([("user", prompt)])
        return QuarterlyReport(
            symbol=result.symbol.upper() or symbol,
            quarter=result.quarter,
            fiscal_year_end=result.fiscal_year_end,
            revenue_npr=result.revenue_npr,
            net_profit_npr=result.net_profit_npr,
            eps_npr=result.eps_npr,
            book_value_npr=result.book_value_npr,
            p_e_ratio=result.p_e_ratio,
            p_b_ratio=result.p_b_ratio,
            roe_pct=result.roe_pct,
            roa_pct=result.roa_pct,
            npl_ratio_pct=result.npl_ratio_pct,
            cap_adequacy_pct=result.cap_adequacy_pct,
            distributable_profit_npr=result.distributable_profit_npr,
            sector=result.sector or "Unknown",
            source="gemini_grounded",
            fetched_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Fetch failed for %s: %s", symbol, e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--top10", action="store_true")
    args = ap.parse_args()
    syms = args.symbols or (TOP_10 if args.top10 else TOP_10)
    logger.info("Fetching fundamentals for %d symbols", len(syms))
    saved = 0
    for s in syms:
        rep = fetch_one(s)
        if rep and rep.quarter:
            save_report(rep)
            saved += 1
            logger.info("  %s %s: EPS %.2f  ROE %.1f%%  NPL %.1f%%  sector=%s",
                        rep.symbol, rep.quarter, rep.eps_npr, rep.roe_pct,
                        rep.npl_ratio_pct, rep.sector)
    logger.info("Done: %d/%d fundamentals saved", saved, len(syms))


if __name__ == "__main__":
    main()
