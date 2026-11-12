"""Analytics API — aggregated metrics for the Analytics view.

  GET /api/analytics/abstention — per-ticker hold rate + mean conf on
                                  HOLDs vs trades, across the reference run.

The research angle: a pipeline that *selects when to abstain* is more
useful than one that trades every day with high confidence. This endpoint
is the raw material for the paper's "selective-prediction" narrative.
"""
from __future__ import annotations

import logging
import statistics
import sys
from pathlib import Path

from fastapi import APIRouter

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

NASDAQ_ROOT = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _per_ticker_abstention(root: Path) -> list[dict]:
    from scripts.eval_nasdaq import _load_ticker_records
    rows: list[dict] = []
    if not root.exists():
        return rows
    for tdir in sorted(root.iterdir()):
        if not tdir.is_dir():
            continue
        ticker = tdir.name
        records = _load_ticker_records(root, ticker)
        good = [r for r in records if not r.get("error") and r.get("date")]
        if not good:
            continue
        n_hold = n_buy = n_sell = 0
        hold_confs: list[float] = []
        trade_confs: list[float] = []
        for r in good:
            fd = r.get("final_decision") or {}
            action = str(fd.get("action") or "").lower()
            conf = fd.get("confidence")
            is_num = isinstance(conf, (int, float))
            if action == "buy":
                n_buy += 1
                if is_num:
                    trade_confs.append(float(conf))
            elif action == "sell":
                n_sell += 1
                if is_num:
                    trade_confs.append(float(conf))
            else:
                n_hold += 1
                if is_num:
                    hold_confs.append(float(conf))
        n = len(good)
        rows.append({
            "ticker": ticker,
            "n": n,
            "n_hold": n_hold,
            "n_buy": n_buy,
            "n_sell": n_sell,
            "hold_rate": round(n_hold / n, 3) if n else 0.0,
            "mean_conf_on_hold": round(statistics.mean(hold_confs), 3) if hold_confs else None,
            "mean_conf_on_trade": round(statistics.mean(trade_confs), 3) if trade_confs else None,
        })
    return rows


@router.get("/abstention")
def abstention() -> dict:
    rows = _per_ticker_abstention(NASDAQ_ROOT)
    return {
        "source": "logs/decisions/nasdaq_focus",
        "n_tickers": len(rows),
        "tickers": rows,
    }
