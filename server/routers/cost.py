"""Cost meter API.

  GET /api/cost/today — aggregate token usage across today's live journals

Walks `logs/decisions/live/<market>/<ticker>/<TICKER>_<TODAY>.jsonl` and
sums `token_usage.totals` across every record. Rough USD estimate uses a
single placeholder ($0.005 per 1k tokens) — real per-model pricing moves
to Phase E.2 of a follow-up when the pricing table matures.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_DIR = REPO_ROOT / "logs" / "decisions" / "live"

# Placeholder blended price ($0.005 / 1k tokens). Replace with a model
# routing table once per-model USD is tracked at journal time.
USD_PER_1K_TOKENS = 0.005

router = APIRouter(prefix="/api/cost", tags=["cost"])


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _walk_today(today: str):
    """Yield every journal path for today across all markets/tickers."""
    if not LIVE_DIR.exists():
        return
    for market_dir in LIVE_DIR.iterdir():
        if not market_dir.is_dir():
            continue
        for ticker_dir in market_dir.iterdir():
            if not ticker_dir.is_dir():
                continue
            fname = f"{ticker_dir.name}_{today}.jsonl"
            path = ticker_dir / fname
            if path.exists():
                yield path


def _records_in(path: Path):
    import json
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
    except OSError:
        return


@router.get("/today")
def cost_today() -> dict:
    today = _today_iso()
    inp = out = calls = n_records = 0
    for path in _walk_today(today):
        for rec in _records_in(path):
            totals = ((rec.get("token_usage") or {}).get("totals") or {})
            inp += int(totals.get("input_tokens") or 0)
            out += int(totals.get("output_tokens") or 0)
            calls += int(totals.get("calls") or 0)
            n_records += 1
    total = inp + out
    usd = (total / 1000.0) * USD_PER_1K_TOKENS
    return {
        "date": today,
        "n_records": n_records,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": total,
        "calls": calls,
        "usd_estimate": round(usd, 4),
        "usd_per_1k_tokens": USD_PER_1K_TOKENS,
    }
