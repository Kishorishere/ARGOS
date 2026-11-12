"""News feed — Finnhub company-news on a free API key.

Config: set `FINNHUB_API_KEY` in `.env`. When the key is missing or an
error occurs, every function returns an empty result with a structured
`error` so the UI degrades to a clean placeholder instead of crashing.

Finnhub's free tier gives ~60 req/min and covers US-listed equities.
The `category` field is a plain string Finnhub assigns ("company news",
"press release", …). We expose it as-is and let the Sentiment analyst
(and the news trigger) decide what's material.

Short-lived in-memory + disk cache (5 min) keeps panel refresh snappy
without burning the free quota on quick ticker-to-ticker navigation.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "cache" / "news"
CACHE_TTL_S = 5 * 60

FINNHUB_URL = "https://finnhub.io/api/v1/company-news"

# Categories we treat as "material" by default when firing news triggers.
# Finnhub's taxonomy is loose so we match case-insensitively on substring.
MATERIAL_CATEGORIES = (
    "earnings", "mergers", "acquisition", "guidance",
    "sec filing", "legal", "product launch", "downgrade", "upgrade",
)


@dataclass(frozen=True)
class NewsItem:
    ticker: str
    headline: str
    summary: str
    source: str
    category: str
    url: str
    datetime: str          # ISO
    image: Optional[str]
    related: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _cache_path(ticker: str, from_: str, to: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}_{from_}_{to}.json"


def _read_cache(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL_S:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _write_cache(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(items), encoding="utf-8")
    except OSError as e:
        logger.debug("news cache write failed %s: %s", path, e)


def recent_news(ticker: str, days: int = 7, limit: int = 20) -> dict:
    """Return a dict `{ticker, items, error?}`.

    Call shape kept uniform (not a bare list) so the API route can tell
    the frontend why a response is empty — missing key vs no items vs
    http error. The UI shows the reason inline.
    """
    key = os.environ.get("FINNHUB_API_KEY")
    today = datetime.utcnow().date()
    from_ = (today - timedelta(days=max(1, days))).isoformat()
    to = today.isoformat()

    if not key:
        return {"ticker": ticker.upper(), "from": from_, "to": to,
                "items": [], "error": "FINNHUB_API_KEY not set"}

    cache = _read_cache(_cache_path(ticker, from_, to))
    if cache is not None:
        return {"ticker": ticker.upper(), "from": from_, "to": to,
                "items": cache[:limit], "cached": True}

    try:
        r = requests.get(FINNHUB_URL, params={
            "symbol": ticker.upper(), "from": from_, "to": to, "token": key,
        }, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ticker": ticker.upper(), "from": from_, "to": to,
                "items": [], "error": f"finnhub_fetch_failed: {e}"}

    if not isinstance(data, list):
        return {"ticker": ticker.upper(), "from": from_, "to": to,
                "items": [], "error": "finnhub_bad_response"}

    items: list[dict] = []
    for d in data:
        if not isinstance(d, dict):
            continue
        ts = d.get("datetime")
        iso = ""
        try:
            if isinstance(ts, (int, float)):
                iso = datetime.utcfromtimestamp(ts).isoformat(timespec="seconds") + "Z"
        except Exception:  # noqa: BLE001
            iso = ""
        items.append(NewsItem(
            ticker=ticker.upper(),
            headline=str(d.get("headline") or ""),
            summary=str(d.get("summary") or ""),
            source=str(d.get("source") or ""),
            category=str(d.get("category") or ""),
            url=str(d.get("url") or ""),
            datetime=iso,
            image=str(d.get("image") or "") or None,
            related=str(d.get("related") or "") or None,
        ).to_dict())

    items.sort(key=lambda x: x.get("datetime") or "", reverse=True)
    _write_cache(_cache_path(ticker, from_, to), items)
    return {"ticker": ticker.upper(), "from": from_, "to": to,
            "items": items[:limit], "cached": False}


def is_material(item: dict) -> bool:
    """Trigger-side helper: is this news item material?"""
    cat = (item.get("category") or "").lower()
    head = (item.get("headline") or "").lower()
    for word in MATERIAL_CATEGORIES:
        if word in cat or word in head:
            return True
    return False


__all__ = ["NewsItem", "MATERIAL_CATEGORIES", "recent_news", "is_material"]
