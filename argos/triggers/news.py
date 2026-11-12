"""News triggers — Finnhub material news within a short window.

Phase C upgrade from the Phase B stub. One rule:

  material_news_within_window — Finnhub returned ≥1 news item for this
    ticker in the last WINDOW_HOURS whose category or headline matches
    argos.data.news.is_material (earnings, M&A, guidance, downgrade,
    upgrade, …).

Fires neutrally — a trigger that *something happened* belongs to the
event layer, not the direction layer. The committee's Sentiment analyst
is the one that decides whether the news is bullish or bearish.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from argos.triggers.base import Trigger

logger = logging.getLogger(__name__)

WINDOW_HOURS = 24
MIN_MATERIAL_HITS = 1


def check(ticker: str, as_of: str) -> list[Trigger]:
    try:
        from argos.data.news import is_material, recent_news
        resp = recent_news(ticker, days=2, limit=50)
    except Exception as e:  # noqa: BLE001
        logger.debug("news trigger fetch failed on %s: %s", ticker, e)
        return []

    items = resp.get("items") or []
    if not items:
        return []

    cutoff = datetime.utcnow() - timedelta(hours=WINDOW_HOURS)
    hits = []
    for it in items:
        ts = it.get("datetime") or ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", ""))
        except ValueError:
            continue
        if dt < cutoff:
            continue
        if is_material(it):
            hits.append(it)

    if len(hits) < MIN_MATERIAL_HITS:
        return []

    cats = {}
    for it in hits:
        cat = (it.get("category") or "?").lower()
        cats[cat] = cats.get(cat, 0) + 1
    top = max(hits, key=lambda it: it.get("datetime") or "")
    detail = (
        f"{len(hits)} material news item(s) in last {WINDOW_HOURS}h: " +
        ", ".join(f"{k}:{v}" for k, v in cats.items()) +
        f" · latest: '{(top.get('headline') or '')[:80]}'"
    )
    return [Trigger(
        ticker=ticker.upper(), as_of=as_of,
        kind="material_news_within_window", direction="neutral",
        magnitude=float(len(hits)), detail=detail, source="news",
    )]
