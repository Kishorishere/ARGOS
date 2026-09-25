"""Nepali-language news sentiment for NEPSE tickers (P10).

Design:
  1. Per-symbol article scraper — search ShareSansar news + Merolagani
     news + ekantipur / karobardaily / bikaskonews RSS/HTML for the
     ticker's name or Nepali company name
  2. Translation + scoring via Gemini (which handles Nepali well)
  3. Aggregate daily sentiment score per ticker in [-1..+1]

Cost: 1 LLM call per (symbol, day) for translate + score. On our free
tier we can afford this only for the ~10 symbols in the screener's
top-K per day (~10 LLM calls/day, negligible).

For the first cut this module ships a functional storage + score-
aggregation API + Gemini-backed scoring, with a stub article-fetcher.
Real fetching added when we prioritize (~4h work).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SENTIMENT_DIR = Path(__file__).resolve().parents[2] / "data" / "nepali_sentiment"


@dataclass
class NewsArticle:
    """One news article about a ticker."""
    fetched_at: str
    symbol: str
    source: str              # "ekantipur" | "karobardaily" | "sharesansar" | ...
    url: str
    headline_np: str          # Nepali headline
    headline_en: str = ""     # English translation (may be empty)
    body_snippet: str = ""    # first 500 chars
    published_date: str = ""


@dataclass
class SentimentScore:
    """One sentiment reading (Nepali news scored by LLM)."""
    date: str
    symbol: str
    score: float              # -1 (very bearish) .. +1 (very bullish)
    n_articles: int
    top_headline: str
    reasoning: str            # LLM's take on why the news is bull/bear
    scored_at: str


def _path_for(symbol: str) -> Path:
    SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)
    return SENTIMENT_DIR / f"{symbol.upper()}.jsonl"


def save_sentiment(score: SentimentScore) -> None:
    p = _path_for(score.symbol)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(score.__dict__) + "\n")


def latest_sentiment(symbol: str, max_age_days: int = 3) -> Optional[SentimentScore]:
    """Return the most-recent sentiment reading if it's fresh enough."""
    p = _path_for(symbol)
    if not p.exists():
        return None
    latest: Optional[SentimentScore] = None
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if latest is None or row["date"] > latest.date:
                latest = SentimentScore(**row)
        except Exception:
            continue
    if not latest:
        return None
    try:
        age = (date.today() - datetime.strptime(latest.date, "%Y-%m-%d").date()).days
    except ValueError:
        age = 999
    return latest if age <= max_age_days else None


def sentiment_score(symbol: str) -> float:
    """[-1..+1] sentiment score for the ensemble.

    Preference order:
      1. Fresh Gemini-scored article sentiment (data/nepali_sentiment/{SYM}.jsonl)
      2. Announcement-derived keyword score from the NEPSE API feed
         (data/announcements/score_by_symbol.json) — always available
      3. 0.0 (neutral)
    """
    sc = latest_sentiment(symbol)
    if sc:
        return sc.score
    try:
        from argos.data.announcements import announcement_score
        return announcement_score(symbol)
    except Exception:
        return 0.0


# ── Stub fetcher + scorer ────────────────────────────────────────────

def fetch_articles(symbol: str) -> list[NewsArticle]:
    """Placeholder. Real implementation:

    - ShareSansar `/company/{SYMBOL}/news` — HTML with recent news links
    - Merolagani `/CompanyDetail.aspx?symbol={SYMBOL}` also lists news
    - Ekantipur / karobardaily / bikaskonews: search by company name (Nepali)
    - Return list[NewsArticle] with headline + snippet

    Then `score_articles_with_gemini()` batches them (~500 chars each),
    asks Gemini to translate + score sentiment 0-100 per article, and
    aggregates to a daily SentimentScore.
    """
    logger.info("Nepali sentiment scraper is a stub for %s.", symbol)
    return []


def score_articles_with_gemini(symbol: str, articles: list[NewsArticle]) -> Optional[SentimentScore]:
    """Placeholder. Feeds articles to Gemini in a batch prompt like:

        "You are a Nepali-market news analyst. For each headline+snippet
        below, score sentiment 1..100 (1=very bearish, 100=very bullish
        for {symbol}). Also give an aggregate score and one-line rationale."

    Returns a SentimentScore.
    """
    if not articles:
        return None
    logger.info("Nepali sentiment scorer is a stub for %s (%d articles).",
                symbol, len(articles))
    return None
