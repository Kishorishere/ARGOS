"""Piotroski-style F-score + sector-relative ranking for NEPSE.

The classic Piotroski F-score is 9 binary tests on fundamentals; a
company scoring 8-9 outperforms the market by ~7%/year historically.
We adapt it to what's typically available on NEPSE quarterly reports
and add sector-relative percentile ranking so a company's score is
meaningful vs peers, not vs global equities.

Output: score in [0..100] representing sector-relative percentile.
"""

from __future__ import annotations

import logging
from typing import Optional

from argos.fundamentals.store import (
    QuarterlyReport,
    latest_quarter,
    load_reports,
    load_universe_latest,
)

logger = logging.getLogger(__name__)


def _piotroski_signals(prev: Optional[QuarterlyReport],
                       curr: QuarterlyReport) -> dict[str, int]:
    """Piotroski-like 9-point signal set adapted for NEPSE data."""
    signals: dict[str, int] = {}

    # Profitability
    signals["positive_net_profit"] = int(curr.net_profit_npr > 0)
    signals["positive_roa"] = int(curr.roa_pct > 0)
    signals["positive_roe"] = int(curr.roe_pct > 0)

    if prev:
        signals["profit_growing"] = int(curr.net_profit_npr > prev.net_profit_npr)
        signals["roe_growing"] = int(curr.roe_pct > prev.roe_pct)
        signals["eps_growing"] = int(curr.eps_npr > prev.eps_npr)
    else:
        signals.update({"profit_growing": 0, "roe_growing": 0, "eps_growing": 0})

    # Nepal-specific: NPL ratio decrease is bullish for banks
    if prev and curr.npl_ratio_pct > 0 and prev.npl_ratio_pct > 0:
        signals["npl_improving"] = int(curr.npl_ratio_pct < prev.npl_ratio_pct)
    else:
        signals["npl_improving"] = 0

    signals["adequate_capital"] = int(curr.cap_adequacy_pct >= 11.0)  # NRB min ~11%
    signals["positive_distributable"] = int(curr.distributable_profit_npr > 0)

    return signals


def compute_f_score(symbol: str) -> Optional[dict]:
    """Raw Piotroski-like F-score (0-9) + explanation. None if no data."""
    reports = sorted(load_reports(symbol), key=lambda r: r.quarter)
    if not reports:
        return None
    curr = reports[-1]
    prev = reports[-2] if len(reports) >= 2 else None
    signals = _piotroski_signals(prev, curr)
    raw = sum(signals.values())
    return {
        "symbol": symbol,
        "quarter": curr.quarter,
        "raw_score": raw,
        "max_possible": len(signals),
        "signals": signals,
        "grade": ("strong" if raw >= 7 else "neutral" if raw >= 5 else "weak"),
    }


def sector_rank(symbol: str) -> Optional[dict]:
    """Percentile rank of `symbol` within its sector on 4 key metrics.

    Returns:
        {
          "symbol": "NABIL",
          "sector": "Commercial Banks",
          "peers": 21,
          "roe_percentile": 78,
          "eps_percentile": 82,
          "profit_growth_percentile": 65,
          "npl_percentile": 90,   # inverted — lower NPL = higher pct
          "composite_score": 78.75,   # avg of the four
          "grade": "top"
        }
    """
    my = latest_quarter(symbol)
    if not my:
        return None
    universe = load_universe_latest()
    peers = [r for s, r in universe.items()
             if r.sector == my.sector and s.upper() != symbol.upper()]
    if not peers:
        return None

    def _pct(value: float, values: list[float], invert: bool = False) -> float:
        if not values or value is None:
            return 50.0
        vals = sorted(values + [value])
        rank = vals.index(value) + 1
        pct = 100 * rank / len(vals)
        return 100 - pct if invert else pct

    roe_pct = _pct(my.roe_pct, [p.roe_pct for p in peers])
    eps_pct = _pct(my.eps_npr, [p.eps_npr for p in peers])
    # Profit growth = last-quarter delta
    prior = load_reports(symbol)
    if len(prior) >= 2:
        prior_sorted = sorted(prior, key=lambda r: r.quarter)
        my_growth = prior_sorted[-1].net_profit_npr - prior_sorted[-2].net_profit_npr
    else:
        my_growth = 0.0
    peer_growths = []
    for p in peers:
        pr = load_reports(p.symbol)
        if len(pr) >= 2:
            pr = sorted(pr, key=lambda r: r.quarter)
            peer_growths.append(pr[-1].net_profit_npr - pr[-2].net_profit_npr)
    growth_pct = _pct(my_growth, peer_growths)
    npl_pct = _pct(my.npl_ratio_pct, [p.npl_ratio_pct for p in peers], invert=True)

    composite = (roe_pct + eps_pct + growth_pct + npl_pct) / 4
    grade = ("top" if composite >= 70 else "mid" if composite >= 40 else "bottom")

    return {
        "symbol": symbol,
        "sector": my.sector,
        "peers": len(peers),
        "roe_percentile": round(roe_pct, 1),
        "eps_percentile": round(eps_pct, 1),
        "profit_growth_percentile": round(growth_pct, 1),
        "npl_percentile": round(npl_pct, 1),
        "composite_score": round(composite, 1),
        "grade": grade,
    }
