"""Thin per-ticker decision index — the feed for chart overlays.

  GET /api/decisions/{ticker}?from=YYYY-MM-DD&to=YYYY-MM-DD&include_live=true

Walks every journal file for a ticker across all markets AND the Phase 0
live namespace (logs/decisions/live/{market}/{ticker}/), returning a thin
array of {as_of, action, confidence, gate_state, source, journal_path} rows.
The per-ticker chart overlays decision dots using this.

Sorted by as_of ascending. Latest attempt per (ticker, date) wins.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISIONS = REPO_ROOT / "logs" / "decisions"
NASDAQ_DIR = DECISIONS / "nasdaq_focus"
LIVE_DIR = DECISIONS / "live"
WF_DIR = DECISIONS / "wf"
PAPER_DIR = DECISIONS / "paper"

_FILE_RE = re.compile(r"^(?P<ticker>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.jsonl$")

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


def _candidate_files(ticker: str) -> list[tuple[str, Path, str]]:
    """Return (source, path, date) for every journal file matching this ticker."""
    ticker_up = ticker.upper()
    out: list[tuple[str, Path, str]] = []

    # 1. Backtest reference run — nasdaq_focus/{TICKER}/{TICKER}_{DATE}.jsonl
    nasdaq_ticker_dir = NASDAQ_DIR / ticker_up
    if nasdaq_ticker_dir.is_dir():
        for f in nasdaq_ticker_dir.glob(f"{ticker_up}_*.jsonl"):
            m = _FILE_RE.match(f.name)
            if m:
                out.append(("backtest", f, m.group("date")))

    # 2. Legacy / flat namespace — logs/decisions/{TICKER}_{DATE}.jsonl
    if DECISIONS.is_dir():
        for f in DECISIONS.glob(f"{ticker_up}_*.jsonl"):
            m = _FILE_RE.match(f.name)
            if m:
                out.append(("legacy", f, m.group("date")))

    # 3. Live namespace — logs/decisions/live/{MARKET}/{TICKER}/{TICKER}_{DATE}.jsonl
    # 4. Walk-forward — logs/decisions/wf/{MARKET}/{TICKER}/...
    # 5. Paper       — logs/decisions/paper/{MARKET}/{TICKER}/...
    for root, source in ((LIVE_DIR, "live"), (WF_DIR, "walk_forward"), (PAPER_DIR, "paper")):
        if not root.is_dir():
            continue
        for market_dir in root.iterdir():
            if not market_dir.is_dir():
                continue
            tdir = market_dir / ticker_up
            if not tdir.is_dir():
                continue
            for f in tdir.glob(f"{ticker_up}_*.jsonl"):
                m = _FILE_RE.match(f.name)
                if m:
                    out.append((source, f, m.group("date")))

    return out


def _latest_attempt(path: Path) -> dict | None:
    """Return the last complete record in a JSONL file, or None."""
    try:
        last: dict | None = None
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
        return last
    except OSError:
        return None


def _stage_for(source: str) -> str:
    """Trust tier of the journal namespace. Front-end renders this as a chip.

      backtest     → historical replay (nasdaq_focus/), lowest trust
      walk_forward → in-sample tuned → out-of-sample scored
      paper        → live data, no money moving
      live         → live committee call this session; highest realized trust
      legacy       → pre-Phase-0 flat namespace; treat like backtest
    """
    return {
        "backtest": "backtest",
        "walk_forward": "walk_forward",
        "paper": "paper",
        "live": "live",
        "legacy": "backtest",
    }.get(source, "backtest")


def _summarize(rec: dict, source: str) -> dict:
    final = rec.get("final_decision") or {}
    gate = rec.get("gate_verdict") or {}
    return {
        "action": final.get("action"),
        "confidence": final.get("confidence"),
        "position_size": final.get("position_size"),
        "gate_proceed": gate.get("proceed"),
        "gate_skipped": (gate.get("proceed") is False),
        "attempt_id": rec.get("attempt_id"),
        "argos_version": rec.get("argos_version"),
        "stage": _stage_for(source),
        "n_triggers": len(rec.get("triggers") or []),
    }


@router.get("/{ticker}")
def ticker_decisions(
    ticker: str,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    include_live: bool = Query(True),
    sources: str | None = Query(None, description="Comma-separated: backtest,live,legacy"),
) -> dict:
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="ticker required")
    ticker = ticker.upper()

    wanted = {s.strip() for s in (sources or "backtest,live,legacy").split(",") if s.strip()}
    if not include_live:
        wanted.discard("live")

    rows: list[dict] = []
    for source, path, date in _candidate_files(ticker):
        if source not in wanted:
            continue
        if from_ and date < from_:
            continue
        if to and date > to:
            continue
        rec = _latest_attempt(path)
        if not rec:
            continue
        summary = _summarize(rec, source)
        summary.update({
            "ticker": ticker,
            "as_of": date,
            "source": source,
            "journal_path": str(path.relative_to(REPO_ROOT)),
        })
        rows.append(summary)

    rows.sort(key=lambda r: (r["as_of"], r["source"]))
    return {
        "ticker": ticker,
        "from": from_,
        "to": to,
        "sources": sorted(wanted),
        "n": len(rows),
        "decisions": rows,
    }
