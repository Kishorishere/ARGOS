"""
OHLCV Data Loader — wraps yfinance with a no-lookahead guard.

§6.2 of the build spec:
"A single data-loading function is the only place in the codebase allowed to
read from raw/. It takes an explicit as_of date and hard-fails if any request
would return data timestamped after it."

This module is that single chokepoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# How many days of history to fetch before as_of_date
DEFAULT_LOOKBACK_DAYS = 90


def load_ohlcv(
    ticker: str,
    as_of_date: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict:
    """Load OHLCV data for a ticker, strictly limited to data up to as_of_date.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        as_of_date: The evaluation date in "YYYY-MM-DD" format. No data after this date.
        lookback_days: How many calendar days of history to fetch before as_of_date.

    Returns:
        Dict with keys: ticker, as_of_date, ohlcv (list of dicts), and metadata.

    Raises:
        ValueError: If as_of_date is in the future or data is empty.
    """
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d")

    # No-lookahead guard: hard-fail if as_of_date is today or future
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if as_of >= today:
        raise ValueError(
            f"as_of_date ({as_of_date}) must be strictly in the past. "
            f"This prevents data leakage in backtesting."
        )

    start_date = as_of - timedelta(days=lookback_days)
    end_date = as_of + timedelta(days=1)  # yfinance end is exclusive

    logger.info(
        "Loading OHLCV for %s from %s to %s",
        ticker,
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
    )

    df = yf.download(
        ticker,
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
    )

    if df.empty:
        raise ValueError(
            f"No OHLCV data found for {ticker} in range "
            f"{start_date.date()} to {as_of.date()}"
        )

    # Flatten MultiIndex columns if present (yfinance sometimes adds them)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Final no-lookahead check: no row should have a date after as_of_date
    last_row_date = df.index[-1].to_pydatetime().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if last_row_date > as_of:
        raise ValueError(
            f"Data leakage detected! Last row date {last_row_date.date()} "
            f"is after as_of_date {as_of.date()}"
        )

    # Convert to list of dicts for easy serialization
    ohlcv_records = []
    for date, row in df.iterrows():
        ohlcv_records.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
        })

    logger.info("Loaded %d OHLCV rows for %s", len(ohlcv_records), ticker)

    return {
        "ticker": ticker,
        "as_of_date": as_of_date,
        "ohlcv": ohlcv_records,
        "rows": len(ohlcv_records),
        "date_range": f"{ohlcv_records[0]['date']} to {ohlcv_records[-1]['date']}",
    }
