"""Announcement → price-reaction gap (P27).

Post-announcement drift is a documented anomaly on retail-heavy exchanges:
positive company news is UNDER-reacted in the first 1-3 sessions and the
price continues drifting for a week or more before fair-value is priced
in. On NEPSE the drift window is even longer (T+2 settlement, retail-only
attention lag, no institutional flash).

This module scores per-symbol how much the price HAS or HAS NOT reacted
to recent bullish/bearish announcements. A stock with a fresh +1.5
announcement score but flat price for 3 sessions is a bullish drift bet.

Signals produced:
  - `days_since_last_signal(symbol)`  — most recent non-neutral news day
  - `announcement_price_gap(symbol, as_of, ohlcv)` — signed drift edge
     +1.0 = bullish news fired 1-5 sessions ago and price is FLAT/DOWN
            (under-reaction — expect drift up)
     -1.0 = bearish news fired 1-5 sessions ago and price is FLAT/UP
            (denial — expect drift down)
     0.0 = news + price already aligned OR no recent news
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

ANN_DIR = Path(__file__).resolve().parents[2] / "data" / "announcements"

# Weight per news-score point → expected pct move (calibrated from Nepal
# post-earnings drift: ~2.5% per +1 sentiment score over next 5 sessions)
EXPECTED_MOVE_PER_SCORE_PCT = 2.5
DRIFT_WINDOW_DAYS = 5


def _load_recent_announcements(symbol: str, window_days: int = DRIFT_WINDOW_DAYS + 2) -> list[dict]:
    """Return raw announcement dicts for `symbol` filed in the last `window_days`."""
    if not ANN_DIR.exists():
        return []
    cutoff = date.today() - timedelta(days=window_days)
    out: list[dict] = []
    for jf in sorted(ANN_DIR.glob("*.jsonl")):
        try:
            file_date = datetime.strptime(jf.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        for line in jf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                a = json.loads(line)
            except Exception:
                continue
            if (a.get("symbol") or "").upper() == symbol.upper():
                a["_file_date"] = file_date.isoformat()
                out.append(a)
    return out


def days_since_last_signal(symbol: str, window_days: int = DRIFT_WINDOW_DAYS + 2) -> int | None:
    """Sessions since the most recent non-neutral announcement (or None)."""
    anns = _load_recent_announcements(symbol, window_days)
    if not anns:
        return None
    latest = max(a["_file_date"] for a in anns)
    latest_date = datetime.strptime(latest, "%Y-%m-%d").date()
    return (date.today() - latest_date).days


def announcement_price_gap(symbol: str, as_of: str,
                           ohlcv_records: list[dict],
                           drift_window: int = DRIFT_WINDOW_DAYS) -> float | None:
    """Compare EXPECTED price move (from news sentiment) vs ACTUAL move.

    Returns [-1, +1] gap:
      +1 = bullish news fired ≤`drift_window` sessions ago, price hasn't
           kept up  → drift-up edge
      -1 = bearish news fired ≤`drift_window` sessions ago, price hasn't
           fallen → drift-down edge
       0 = no news OR price already fully reacted

    Requires the last ~`drift_window`+3 sessions of ohlcv_records to
    compute realized return.
    """
    try:
        from argos.data.announcements import announcement_score
        news_score = announcement_score(symbol)
    except Exception:
        return None
    if abs(news_score) < 0.05:
        return 0.0
    if len(ohlcv_records) < drift_window + 1:
        return None
    # Compute realized N-day return
    closes = [r.get("close") or 0 for r in ohlcv_records[-drift_window - 1:]]
    if closes[0] <= 0:
        return None
    realized_move_pct = (closes[-1] / closes[0] - 1) * 100
    # Expected move from the news score (score is [-1, +1])
    expected_move_pct = news_score * EXPECTED_MOVE_PER_SCORE_PCT
    # Gap = expected − realized. Sign of gap gives drift direction; magnitude
    # scaled to [-1, +1] by dividing by 2× expected.
    if abs(expected_move_pct) < 1e-6:
        return 0.0
    gap_pct = expected_move_pct - realized_move_pct
    # Normalize: full expected move unrealized → 1.0
    signal = gap_pct / (2 * abs(expected_move_pct))
    return round(max(-1.0, min(1.0, signal)), 3)
