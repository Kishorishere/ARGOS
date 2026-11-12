"""Read-only access to the decision journal (logs/decisions/**.jsonl).

Journal layout on disk:
  nasdaq:  logs/decisions/nasdaq_focus/{TICKER}/{TICKER}_{YYYY-MM-DD}.jsonl
  nepse:   logs/decisions/{TICKER}_{YYYY-MM-DD}.jsonl  (flat; filtered to
           tickers present in data/cache/nepse_ohlcv/*.pkl)
  legacy:  logs/decisions/{TICKER}_{YYYY-MM-DD}.jsonl  (everything else flat)

`*_combined.jsonl` bundles are skipped from the per-date tree.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISIONS = REPO_ROOT / "logs" / "decisions"
NASDAQ_DIR = DECISIONS / "nasdaq_focus"
LIVE_DIR = DECISIONS / "live"
WF_DIR = DECISIONS / "wf"
PAPER_DIR = DECISIONS / "paper"
NEPSE_CACHE = REPO_ROOT / "data" / "cache" / "nepse_ohlcv"

MARKETS = ("nasdaq", "nepse", "legacy")
_FILE_RE = re.compile(r"^(?P<ticker>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.jsonl$")

# path → (mtime, n_records, n_errors) — avoids re-reading files on every tree call
_scan_cache: dict[Path, tuple[float, int, int]] = {}


def _nepse_tickers() -> set[str]:
    if not NEPSE_CACHE.is_dir():
        return set()
    return {p.stem.upper() for p in NEPSE_CACHE.glob("*.pkl")}


def _market_files(market: str) -> list[tuple[str, str, Path]]:
    """Yield (ticker, date, path) for per-date journal files in a market."""
    out: list[tuple[str, str, Path]] = []
    nepse = _nepse_tickers()

    if market == "nasdaq":
        if NASDAQ_DIR.is_dir():
            for tdir in sorted(NASDAQ_DIR.iterdir()):
                if not tdir.is_dir():
                    continue
                for f in sorted(tdir.glob("*.jsonl")):
                    m = _FILE_RE.match(f.name)
                    if m:
                        out.append((m.group("ticker"), m.group("date"), f))
    else:
        if not DECISIONS.is_dir():
            return out
        for f in sorted(DECISIONS.glob("*.jsonl")):
            m = _FILE_RE.match(f.name)
            if not m:
                continue
            ticker = m.group("ticker").upper()
            if market == "nepse" and nepse and ticker not in nepse:
                continue
            if market == "legacy" and nepse and ticker in nepse:
                continue
            out.append((ticker, m.group("date"), f))
    return out


def _file_stats(path: Path) -> tuple[int, int]:
    """(records, errors) for one JSONL file, mtime-cached."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return (0, 0)
    cached = _scan_cache.get(path)
    if cached and cached[0] == mtime:
        return (cached[1], cached[2])
    n, errs = 0, 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                n += 1
                if rec.get("error") or not rec.get("final_decision"):
                    errs += 1
    except OSError:
        pass
    _scan_cache[path] = (mtime, n, errs)
    return (n, errs)


def list_market_journals(market: str) -> dict:
    """Tree of {ticker → sorted dates, record/error counts} for one market."""
    if market not in MARKETS:
        raise ValueError(f"market must be one of {MARKETS}")
    tickers: dict[str, dict] = {}
    for ticker, date, path in _market_files(market):
        n, errs = _file_stats(path)
        t = tickers.setdefault(ticker, {"ticker": ticker, "dates": [], "records": 0, "errors": 0})
        t["dates"].append(date)
        t["records"] += n
        t["errors"] += errs
    for t in tickers.values():
        t["dates"].sort()
    root = NASDAQ_DIR if market == "nasdaq" else DECISIONS
    return {
        "market": market,
        "root": str(root.relative_to(REPO_ROOT)) if root.exists() else str(root),
        "tickers": sorted(tickers.values(), key=lambda t: t["ticker"]),
    }


def journal_path(market: str, ticker: str, date: str) -> Path | None:
    """Resolve a (market, ticker, date) tuple to its on-disk journal.

    Searches every stage namespace in a known order and returns the
    first match. The frontend's JournalDrawer doesn't know which stage
    a historical decision came from — this walk makes the one route
    serve all four stages (backtest / live / walk-forward / paper) and
    the legacy flat layout.
    """
    ticker = ticker.upper()
    fname = f"{ticker}_{date}.jsonl"
    candidates: list[Path] = []
    if market == "nasdaq":
        candidates.append(NASDAQ_DIR / ticker / fname)
    # Stage namespaces share shape: <root>/<market>/<ticker>/<ticker>_<date>.jsonl
    for root in (LIVE_DIR, PAPER_DIR, WF_DIR):
        candidates.append(root / market / ticker / fname)
    # Legacy flat layout (and nepse / unknown markets)
    candidates.append(DECISIONS / fname)

    for p in candidates:
        if p.exists():
            return p
    return None


def read_attempts(market: str, ticker: str, date: str) -> list[dict]:
    """All append-only attempts for one (ticker, date), in file order."""
    path = journal_path(market, ticker, date)
    if path is None:
        return []
    attempts: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                attempts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return attempts
