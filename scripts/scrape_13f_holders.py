"""Refresh the 13F holders cache for the watchlist universe.

Phase C.2 — run this weekly (cron, GitHub Actions, or by hand after a
13F filing window closes). It:

  1. Walks every ticker in data/universe/nasdaq_focus.json (or the
     --tickers override).
  2. Hits argos.data.edgar.top_institutional_holders which pulls from
     yfinance's `.institutional_holders`.
  3. Writes/refreshes data/cache/edgar/holders/{TICKER}.json.

The Ownership panel reads from this cache on every pageview, so the
scrape is the one place that touches a remote feed. Safe to re-run;
the cache is write-through with a 24h TTL inside the client.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("scrape_13f_holders")

DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh the 13F holders cache (yfinance-sourced)")
    p.add_argument("--tickers", default=None, help="Comma-separated override (default: universe file)")
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    p.add_argument("--sleep", type=float, default=0.75, help="Seconds between tickers (be polite to yfinance)")
    return p.parse_args()


def main() -> None:
    from argos.data.edgar import top_institutional_holders

    args = _parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else \
              json.loads(Path(args.universe).read_text())["tickers"]
    logger.info("refreshing 13F holders for %d tickers", len(tickers))

    totals = []
    for i, t in enumerate(tickers):
        try:
            rows = top_institutional_holders(t, limit=25)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s failed: %s", t, e)
            rows = []
        logger.info("  %-6s %d holders", t, len(rows))
        totals.append({"ticker": t, "n": len(rows)})
        if i < len(tickers) - 1 and args.sleep > 0:
            time.sleep(args.sleep)

    out_path = REPO_ROOT / "data" / "cache" / "edgar" / "holders_index.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"universe": tickers, "totals": totals}, indent=2), encoding="utf-8")
    logger.info("wrote %s", out_path)


if __name__ == "__main__":
    main()
