"""NEPSE-wide indicator screener.

The 44-date one-ticker backtest revealed: Argos usually stays flat on any
single stock because the daily setup is boring. But on any given trading
day, SOME NEPSE stock IS setting up cleanly. The screener flips the
framing:

    Given N tradeable symbols today, rank them by cheap indicator score
    (no LLM). Return the top K bullish + top K bearish candidates.
    The full 7-agent stack then runs ONLY on those top candidates.

Result: instead of ~1 directional bet per week per ticker, we get ~3
quality bets per day out of the whole market.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from argos.data.nepse_api import fetch_market_snapshot
from argos.data.nepse_ohlcv import load_nepse_ohlcv
from argos.indicators import compute_snapshot

logger = logging.getLogger(__name__)

# Symbols we'll accept from the market snapshot — filters out bonds, mutual
# funds, promoter shares, IPO tranches, and other non-equity items that clog
# NEPSE listings but aren't tradeable in the strategy.
_EXCLUDE_SUFFIXES = ("PO", "PROM", "UR85/86", "GED", "84")   # bond/promoter tags
_MIN_TURNOVER_NPR = 100_000                                  # NPR — kills untraded / illiquid


def get_default_universe(min_turnover: float = _MIN_TURNOVER_NPR) -> list[str]:
    """Pull today's traded NEPSE items and return the tradeable symbols."""
    snap = fetch_market_snapshot()
    items = snap.get("items") or []
    out = []
    for it in items:
        sym = (it.get("symbol") or "").upper().strip()
        if not sym:
            continue
        if any(sym.endswith(s) for s in _EXCLUDE_SUFFIXES):
            continue
        if float(it.get("turnover") or 0) < min_turnover:
            continue
        out.append(sym)
    logger.info("Screener universe: %d tradeable symbols (filtered from %d)",
                len(out), len(items))
    return out


@dataclass
class ScreenerScore:
    symbol: str
    net_bias_score: float
    composite_bull: float
    composite_bear: float
    bull_ringer: float
    bear_ringer: float
    trend_regime: str | None
    vol_regime: str | None
    n_signals: int
    error: str | None = None

    @property
    def combined_score(self) -> float:
        """Signed conviction: positive = bullish, negative = bearish.

        Includes bull_ringer/bear_ringer candle bonus (both weighted 1.0)
        so a strong reversal pattern shows up in the ranking.
        """
        return self.net_bias_score + (self.bull_ringer - self.bear_ringer)


def _score_one(symbol: str, as_of_date: str, lookback_days: int = 400) -> ScreenerScore:
    try:
        data = load_nepse_ohlcv(symbol, as_of_date, lookback_days=lookback_days)
        snap = compute_snapshot(data["ohlcv"], symbol, as_of_date)
        return ScreenerScore(
            symbol=symbol,
            net_bias_score=snap.net_bias_score,
            composite_bull=snap.composite_bull_score,
            composite_bear=snap.composite_bear_score,
            bull_ringer=snap.candles.bull_ringer_score,
            bear_ringer=snap.candles.bear_ringer_score,
            trend_regime=snap.trend.regime,
            vol_regime=snap.volatility.vol_regime,
            n_signals=len(snap.fired_signals),
        )
    except Exception as e:  # noqa: BLE001
        return ScreenerScore(symbol=symbol, net_bias_score=0.0, composite_bull=0.0,
                             composite_bear=0.0, bull_ringer=0.0, bear_ringer=0.0,
                             trend_regime=None, vol_regime=None, n_signals=0,
                             error=str(e)[:100])


def rank_universe(as_of_date: str, symbols: list[str] | None = None,
                  top_k_bullish: int = 5, top_k_bearish: int = 5,
                  lookback_days: int = 400,
                  min_signals: int = 4,
                  ) -> dict:
    """Compute IndicatorSnapshot for each symbol, rank by combined_score.

    Args:
        as_of_date: YYYY-MM-DD; strictly no-lookahead
        symbols: universe to score (defaults to today's NEPSE snapshot)
        top_k_bullish: number of most-positive candidates to keep
        top_k_bearish: number of most-negative candidates to keep
        lookback_days: history for indicator computation
        min_signals: reject symbols with fewer signals fired (noise floor)
    """
    if symbols is None:
        symbols = get_default_universe()
    logger.info("Screener: scoring %d symbols as-of %s", len(symbols), as_of_date)

    scores: list[ScreenerScore] = []
    for i, sym in enumerate(symbols, 1):
        if i % 20 == 0:
            logger.info("  screener: %d/%d scored", i, len(symbols))
        scores.append(_score_one(sym, as_of_date, lookback_days))

    good = [s for s in scores if s.error is None and s.n_signals >= min_signals]
    errored = [s for s in scores if s.error is not None]
    logger.info("Screener: %d scored ok, %d errored, %d below signal-count floor",
                len(good), len(errored), len(scores) - len(good) - len(errored))

    sorted_by_score = sorted(good, key=lambda s: s.combined_score, reverse=True)
    top_bull = sorted_by_score[:top_k_bullish]
    top_bear = list(reversed(sorted_by_score[-top_k_bearish:]))

    return {
        "as_of_date": as_of_date,
        "universe_size": len(symbols),
        "scored_ok": len(good),
        "errored": len(errored),
        "top_bullish": [
            {"symbol": s.symbol, "score": round(s.combined_score, 3),
             "net_bias": round(s.net_bias_score, 3),
             "bull_ringer": round(s.bull_ringer, 2),
             "trend": s.trend_regime, "vol": s.vol_regime,
             "n_signals": s.n_signals}
            for s in top_bull
        ],
        "top_bearish": [
            {"symbol": s.symbol, "score": round(s.combined_score, 3),
             "net_bias": round(s.net_bias_score, 3),
             "bear_ringer": round(s.bear_ringer, 2),
             "trend": s.trend_regime, "vol": s.vol_regime,
             "n_signals": s.n_signals}
            for s in top_bear
        ],
    }
