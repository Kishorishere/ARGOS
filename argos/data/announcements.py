"""NEPSE corporate announcements → per-symbol sentiment signal.

Uses `/api/nepse/announcements` (50 latest announcements from the user's
NEPSE API). Each announcement has: symbol, title, summary, dateAD, type,
category.

We derive a lightweight sentiment score PER SYMBOL from the last N days
using heuristics — no LLM needed. When Gemini quota is available the
`nepali_sentiment` module can rescore with the LLM for accuracy.

Positive keywords (bullish): approved, growth, profit, record, expansion,
buyback, dividend, bonus, right shares issued, listed
Negative keywords (bearish): suspension, halt, penalty, loss, downgrade,
default, delisted, sebon action, forfeited

Storage:
    data/announcements/{YYYY-MM-DD}.jsonl  — raw daily snapshots
    data/announcements/score_by_symbol.json — rolling per-symbol score

The ensemble picks up scores via `argos.data.nepali_sentiment.sentiment_score()`
which now first checks the announcements store before falling back to
Gemini-scored articles.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

ANN_DIR = Path(__file__).resolve().parents[2] / "data" / "announcements"
SCORE_PATH = ANN_DIR / "score_by_symbol.json"

BULL_TERMS = re.compile(
    r"\b(approved|growth|profit|record|expansion|buyback|dividend|bonus|"
    r"right.?share.{0,20}issue|listed|allotment|paid|distribut|earn|"
    r"upgraded?|record.?date|book.?closure)\b",
    re.IGNORECASE,
)
BEAR_TERMS = re.compile(
    r"\b(suspen[sd]|halt|penalty|penalt|loss|downgrad|defaulted?|delist|"
    r"sebon.?action|forfeit|fraud|investigat|violat|revok|withdraw|"
    r"insolven|write.?off|npl|late\s+file)\b",
    re.IGNORECASE,
)
NEUTRAL_CATEGORIES = {"financialDocument", "mutualFund"}  # NAV posts etc. are neutral


def _path_for(d: str) -> Path:
    ANN_DIR.mkdir(parents=True, exist_ok=True)
    return ANN_DIR / f"{d}.jsonl"


def _score_text(text: str) -> int:
    """Signed integer: +1 for each bullish keyword hit, -1 for each bearish."""
    bulls = len(BULL_TERMS.findall(text or ""))
    bears = len(BEAR_TERMS.findall(text or ""))
    return bulls - bears


def ingest_announcements_today() -> int:
    """Fetch today's announcements from the NEPSE API, persist per-day,
    recompute rolling scores per symbol.

    Returns the number of announcements retrieved.
    """
    from argos.data.nepse_api import fetch_announcements
    today = date.today().isoformat()
    try:
        anns = fetch_announcements()
    except Exception as e:  # noqa: BLE001
        logger.warning("Announcements fetch failed: %s", e)
        return 0

    p = _path_for(today)
    with open(p, "w", encoding="utf-8") as f:
        for a in anns:
            f.write(json.dumps(a) + "\n")

    # Rebuild rolling score across last 14 days of ingested files
    recompute_scores(window_days=14)
    return len(anns)


def recompute_scores(window_days: int = 14) -> dict[str, float]:
    """Aggregate per-symbol sentiment score from the last N days of
    ingested announcements. Signed float in [-1, +1] via tanh(x/5) so
    scores > +2 saturate near 1.

    Also weights bullish/bearish keyword hits by recency (newer > older).
    """
    if not ANN_DIR.exists():
        return {}
    cutoff = date.today() - timedelta(days=window_days)
    per_symbol_raw: dict[str, float] = defaultdict(float)
    for jf in sorted(ANN_DIR.glob("*.jsonl")):
        try:
            file_date = datetime.strptime(jf.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        # Recency weight: today = 1.0, N days ago = ~0.5 at N=window/2
        age = (date.today() - file_date).days
        recency = 1.0 / (1 + age * 0.15)
        for line in jf.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                a = json.loads(line)
            except Exception:
                continue
            sym = (a.get("symbol") or "").upper()
            if not sym:
                continue
            # Skip neutral categories (NAV updates)
            if a.get("category") in NEUTRAL_CATEGORIES:
                continue
            text = f"{a.get('title', '')} {a.get('summary', '')}"
            per_symbol_raw[sym] += _score_text(text) * recency

    # Normalize to [-1, +1] via tanh
    import math
    per_symbol_normalized = {
        sym: max(-1.0, min(1.0, math.tanh(v / 5)))
        for sym, v in per_symbol_raw.items()
    }
    SCORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCORE_PATH.write_text(json.dumps(per_symbol_normalized, indent=2))
    logger.info("Announcements: recomputed scores for %d symbols", len(per_symbol_normalized))
    return per_symbol_normalized


def announcement_score(symbol: str) -> float:
    """Return the rolling sentiment score for a symbol; 0 if unknown."""
    if not SCORE_PATH.exists():
        return 0.0
    try:
        scores = json.loads(SCORE_PATH.read_text())
    except Exception:
        return 0.0
    return float(scores.get(symbol.upper(), 0.0))
