"""Historical OHLCV loader for NEPSE symbols via Merolagani.

The user's NEPSE API (nepse.nandisolutions.com) only exposes a snapshot of
today's market — no historical bars. For backtesting we hit Merolagani's
TechnicalChartHandler directly, matching the calling convention that
nepse-quant-terminal already uses in `backend/quant_pro/vendor_api.py`.

Returned shape mirrors what `argos/data/ohlcv_loader.py` produces for
yfinance, so the indicator engine and backtest driver need zero changes.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

MEROLAGANI_URL = "https://merolagani.com/handlers/TechnicalChartHandler.ashx"
DEFAULT_TIMEOUT = 20
_last_call = {"t": 0.0}
_MIN_INTERVAL = 2.0  # be polite to Merolagani; ~0.5 req/s max

# ── Parquet cache ───────────────────────────────────────────────────────────
# Cuts repeat backtest wall-time ~4x. Only bars newer than the cache max date
# are re-fetched; everything else comes from local parquet.
CACHE_DIR = Path(os.environ.get("NEPSE_OHLCV_CACHE",
                                 str(Path(__file__).resolve().parents[2] / "data" / "cache" / "nepse_ohlcv")))


def _cache_path(symbol: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Pickle keeps datetimes + int64 volume intact with no dep beyond stdlib.
    return CACHE_DIR / f"{symbol.upper()}.pkl"


def _load_cached(symbol: str) -> pd.DataFrame:
    p = _cache_path(symbol)
    if not p.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    try:
        return pd.read_pickle(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read %s cache (%s) — starting empty", symbol, e)
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _write_cache(symbol: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    p = _cache_path(symbol)
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    try:
        df.to_pickle(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to write %s cache: %s", symbol, e)


def _rate_wait():
    now = time.monotonic()
    elapsed = now - _last_call["t"]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_call["t"] = time.monotonic()


def _fetch_range(symbol: str, start_epoch: int, end_epoch: int,
                 timeout: int = DEFAULT_TIMEOUT) -> pd.DataFrame:
    """Fetch one Merolagani chunk and return an OHLCV DataFrame."""
    _rate_wait()
    params = {
        "type": "get_advanced_chart",
        "symbol": symbol,
        "resolution": "1D",
        "rangeStartDate": start_epoch,
        "rangeEndDate": end_epoch,
        "isAdjust": "1",
        "currencyCode": "NPR",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"https://merolagani.com/CompanyDetail.aspx?symbol={symbol}",
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = requests.get(MEROLAGANI_URL, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    ct = str(resp.headers.get("content-type") or "").lower()
    if "html" in ct:
        logger.warning("Merolagani returned HTML for %s — empty result", symbol)
        return pd.DataFrame()
    try:
        data = resp.json()
    except ValueError:
        logger.warning("Merolagani returned non-JSON for %s — empty result", symbol)
        return pd.DataFrame()
    if isinstance(data, dict) and str(data.get("s") or "").lower() == "no_data":
        return pd.DataFrame()
    required = {"t", "o", "h", "l", "c", "v"}
    if not required.issubset(data.keys()):
        raise ValueError(f"Unexpected Merolagani schema for {symbol}: keys={list(data)[:8]}")
    if not data["t"]:
        return pd.DataFrame()
    return pd.DataFrame({
        "date": pd.to_datetime(data["t"], unit="s"),
        "open": data["o"],
        "high": data["h"],
        "low": data["l"],
        "close": data["c"],
        "volume": data["v"],
    })


def load_nepse_ohlcv(symbol: str, as_of_date: str, lookback_days: int = 400) -> dict:
    """Return an ohlcv dict in the same shape as argos/data/ohlcv_loader.load_ohlcv().

    Args:
        symbol: NEPSE ticker (e.g., NABIL, ADBL, NIFRA)
        as_of_date: YYYY-MM-DD; strictly no bars later than this
        lookback_days: calendar days of history to pull before as_of_date
    """
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    as_of_eod = as_of + timedelta(days=1) - timedelta(seconds=1)
    start = as_of - timedelta(days=lookback_days)

    # ── Cache path first ────────────────────────────────────────────────
    cached = _load_cached(symbol)
    if not cached.empty:
        cached["date"] = pd.to_datetime(cached["date"])
        cache_max = cached["date"].max()
        # Enough coverage already? Then no fetch needed.
        if cache_max >= as_of and cached["date"].min() <= start:
            df = cached
            logger.debug("%s: cache HIT (%d bars, %s..%s)", symbol, len(df),
                         cached["date"].min().date(), cached["date"].max().date())
        else:
            # Fetch only bars newer than the cache
            fetch_from = max(cache_max + timedelta(days=1), start)
            new_df = _fetch_range(symbol, int(fetch_from.timestamp()), int(as_of_eod.timestamp()))
            if not new_df.empty:
                new_df["date"] = pd.to_datetime(new_df["date"]).dt.floor("D")
                df = pd.concat([cached, new_df], ignore_index=True)
            else:
                df = cached
            logger.debug("%s: cache MISS-INC — appended %d new bars from %s",
                         symbol, 0 if new_df.empty else len(new_df), fetch_from.date())
            _write_cache(symbol, df)
    else:
        df = _fetch_range(symbol, int(start.timestamp()), int(as_of_eod.timestamp()))
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.floor("D")
            _write_cache(symbol, df)
        logger.debug("%s: cache MISS — first fetch (%d bars)", symbol, len(df))

    if df.empty:
        raise ValueError(f"No NEPSE OHLCV for {symbol} in {start.date()}..{as_of.date()}")

    # Normalize + dedupe + apply strict no-lookahead cutoff
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.floor("D")
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    df = df[df["date"] <= as_of].reset_index(drop=True)
    if df.empty:
        raise ValueError(f"All returned {symbol} bars are after {as_of_date}")

    records = [
        {
            "date": row["date"].strftime("%Y-%m-%d"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        }
        for _, row in df.iterrows()
    ]
    logger.info("Loaded %d NEPSE bars for %s (through %s)", len(records), symbol, as_of_date)
    return {
        "ticker": symbol,
        "as_of_date": as_of_date,
        "ohlcv": records,
        "rows": len(records),
        "date_range": f"{records[0]['date']} to {records[-1]['date']}",
        "source": "merolagani",
    }
