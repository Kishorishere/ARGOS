"""Nightly ledger updater — join journal signals with realized returns.

Run:  python scripts/update_signal_ledger.py

Idempotent — only records newer than the ledger's last update are
processed. Prints a summary of top/bottom signals by hit rate.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(r"C:\Users\Dell\Desktop\fuse\A-Multi-Agent-Trading-Research-Desk")
sys.path.insert(0, str(REPO))
load_dotenv(REPO / ".env", override=True)

from argos.data.nepse_ohlcv import load_nepse_ohlcv
from argos.learn.signal_ledger import (
    load_ledger, update_from_journal, MIN_OBS_FOR_WEIGHT,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("ledger_updater")


def _yfinance_style_provider(symbol: str, as_of: str, lookback_days: int = 400):
    return load_nepse_ohlcv(symbol, as_of, lookback_days=lookback_days)


def main():
    journal_dir = REPO / "logs" / "decisions"
    logger.info("Walking journal at %s", journal_dir)
    ledger = update_from_journal(journal_dir, _yfinance_style_provider)

    print()
    print(f"=== Signal Ledger (min_obs={MIN_OBS_FOR_WEIGHT} for weight adjustment) ===")
    print(f"{'Indicator':<30s} {'n':>5s} {'hit1d':>6s} {'hit5d':>6s} {'weight':>7s}  last_seen")
    print('-' * 80)
    # Sort by observations descending
    from argos.learn.signal_ledger import get_weight_multiplier
    stats_sorted = sorted(ledger.signals.items(), key=lambda kv: -kv[1].n)
    for name, st in stats_sorted:
        w = get_weight_multiplier(name, ledger)
        status = ("SILENCED" if w == 0.0 else
                  "boosted" if w > 1.0 else
                  "faded" if w < 1.0 else "")
        print(f"{name[:30]:<30s} {st.n:>5d} {100*st.hit_rate_1d:>5.1f}% "
              f"{100*st.hit_rate_5d:>5.1f}% {w:>6.2f}×  {st.last_seen}  {status}")


if __name__ == "__main__":
    main()
