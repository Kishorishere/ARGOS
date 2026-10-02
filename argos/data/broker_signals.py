"""Read-only bridge to `nepse-quant-terminal`'s broker-signals SQLite.

The terminal maintains a `broker_signals_v2` table populated by its
floorsheet scraper. It carries per-symbol per-date scores we care about
for exits:

  * `pump_score`     — higher = suspicious price-run driven by narrow flow
  * `circular_score` — higher = same brokers repeatedly on both sides

If the terminal DB is not present on disk (either the sibling repo is not
here, or it hasn't been scraped yet), everything here silently returns
None. No writes, ever — this file only reads.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_DB = Path(__file__).resolve().parents[3] / "nepse-quant-terminal" / "data" / "nepse_market_data.db"


def _db_path() -> Path | None:
    """Return the terminal's SQLite path, or None if it isn't reachable."""
    env = os.environ.get("NEPSE_DB_FILE")
    p = Path(env) if env else _DEFAULT_DB
    return p if p.exists() else None


def _connect_ro():
    p = _db_path()
    if p is None:
        return None
    try:
        return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.OperationalError as e:
        logger.debug("broker_signals: failed to open %s (%s)", p, e)
        return None


def broker_scores(symbol: str, as_of_date: str) -> dict[str, float] | None:
    """Fetch pump/circular scores for one (symbol, date).

    Returns None if the DB isn't reachable, the table is missing, or no
    row exists for this (symbol, date).
    """
    conn = _connect_ro()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT pump_score, circular_score, smart_money_score, top_pair_pct "
            "FROM broker_signals_v2 WHERE symbol = ? AND as_of_date = ? LIMIT 1",
            (symbol.upper(), as_of_date),
        ).fetchone()
    except sqlite3.OperationalError as e:
        logger.debug("broker_signals: query failed (%s)", e)
        conn.close()
        return None
    conn.close()
    if row is None:
        return None
    return {
        "pump_score": row[0] if row[0] is not None else 0.0,
        "circular_score": row[1] if row[1] is not None else 0.0,
        "smart_money_score": row[2] if row[2] is not None else 0.0,
        "top_pair_pct": row[3] if row[3] is not None else 0.0,
    }


# Terminal's exit rule (`simple_backtest.py:2297-2306`), verbatim.
CIRC_EXIT_THRESHOLD = 0.35
PUMP_EXIT_THRESHOLD = 0.10
MIN_HOLD_DAYS_BEFORE_PUMP_EXIT = 5


def should_pump_exit(symbol: str, as_of_date: str, bars_held: int) -> tuple[bool, str] | None:
    """Return (True, reason) if the pump-exit rule fires, else (False, ...).

    Returns None when broker scores are unavailable (DB missing / no row) —
    which the caller should treat as "no signal" (not "safe" and not
    "unsafe"), i.e. defer to whatever exit logic follows.
    """
    if bars_held < MIN_HOLD_DAYS_BEFORE_PUMP_EXIT:
        return False, f"held only {bars_held} days (<{MIN_HOLD_DAYS_BEFORE_PUMP_EXIT})"
    scores = broker_scores(symbol, as_of_date)
    if scores is None:
        return None
    circ = scores["circular_score"]
    pump = scores["pump_score"]
    if circ > CIRC_EXIT_THRESHOLD or pump > PUMP_EXIT_THRESHOLD:
        return True, (f"broker pump/circular flag: circ={circ:.2f} "
                      f"(>{CIRC_EXIT_THRESHOLD}), pump={pump:.2f} "
                      f"(>{PUMP_EXIT_THRESHOLD}); held {bars_held} days")
    return False, f"circ={circ:.2f}, pump={pump:.2f} — clean"
