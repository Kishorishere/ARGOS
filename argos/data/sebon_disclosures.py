"""SEBON promoter-sale prior-notice scraper.

Nepal's SEBON requires promoters holding >=5% to give 15-day prior notice
of substantial sales (per the 2026 rule change; see
https://nepsetrading.com/blog/sebons-prior-notice-rule-for-major-shareholders-sparks-debate-on-one-sided
and https://nepallaws.com/insider-trading-in-nepal-upholding-transparency/).

SEBON publishes these on its general `/notices` page — there is no
dedicated feed at this time, and the notices themselves are usually
Nepali-language PDF attachments. This module pulls the /notices list,
filters entries whose titles/summaries look like a promoter-sale prior
notice, and persists candidate rows so downstream event-registry code
(argos.events.registry) can consume them.

Design principles:
- **Fail-quiet.** If SEBON's site is unreachable or the DOM changes,
  the daily ingest chain must continue. Never raise.
- **Idempotent.** Re-running the same day appends nothing new (dedupes
  on (title, notice_date, external_url)).
- **Human-in-the-loop friendly.** The output JSONL preserves the raw
  notice title/URL so a person can verify a candidate later; we do NOT
  hallucinate a quantity or promoter name we didn't scrape.

Storage layout mirrors argos.data.announcements:
    data/announcements/sebon_promoter/{YYYY-MM-DD}.jsonl
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SEBON_BASE = "https://www.sebon.gov.np"
NOTICES_URL = f"{SEBON_BASE}/notices"
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "announcements" / "sebon_promoter"
DEFAULT_TIMEOUT = 20

# Bilingual keyword filter — anything in a SEBON notice title/summary that
# smells like a promoter-share-sale prior notice. Conservative: we prefer
# a false positive (a person can discard) over a false negative (missed
# event → invisible signal). Keep patterns wide.
_PROMOTER_TERMS = re.compile(
    r"(promoter|founder|sanchalak|"
    r"प्रवर्तक|साट्ने|"
    r"sale\s+of.*share|prior\s+notice|"
    r"पूर्व\s*सूचना|शेयर\s*बिक्री)",
    re.IGNORECASE,
)

# NEPSE tickers embedded in a title look like `[NABIL]` or `(NABIL)` or
# just `NABIL` in caps. Match a run of 2-6 upper-case letters near the
# start of a title, as a best-effort attribution.
_TICKER_HINT = re.compile(r"\b([A-Z]{2,6})\b")


def _out_path(d: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR / f"{d}.jsonl"


def _already_seen(path: Path, key: tuple) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (row.get("title"), row.get("notice_date"), row.get("url")) == key:
            return True
    return False


def _fetch_notices_html(page: int = 1) -> str | None:
    """Fetch one page of SEBON's notices. Returns raw HTML or None."""
    url = NOTICES_URL if page <= 1 else f"{NOTICES_URL}?page={page}"
    try:
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0 (argos data ingest)"})
        resp.raise_for_status()
        return resp.text
    except Exception as e:  # noqa: BLE001
        logger.debug("SEBON notices fetch failed (page %d): %s", page, e)
        return None


# ── Very defensive HTML parsing ────────────────────────────────────────────
# SEBON's site is a plain server-rendered CMS. We look for common list
# patterns (table rows or article blocks). Since the DOM may change
# without notice, we regex over the raw HTML for anchor tags whose text
# matches our keyword filter — this survives most CSS-class refactors.

_ANCHOR_RE = re.compile(
    r'<a[^>]+href="([^"]+)"[^>]*>([^<]{4,300})</a>',
    re.IGNORECASE | re.DOTALL,
)
# Best-effort date extraction from nearby text: dd/mm/yyyy or yyyy-mm-dd.
_DATE_NEAR_RE = re.compile(
    r'(20\d{2}[-/]\d{1,2}[-/]\d{1,2})',
)


def _extract_candidates(html: str) -> list[dict]:
    """Regex-scan an HTML page for candidate promoter-sale prior notices.

    Returns a list of dicts with title, url, notice_date (best-effort),
    and an inferred symbol hint (if any).
    """
    out: list[dict] = []
    seen_urls: set[str] = set()
    for m in _ANCHOR_RE.finditer(html):
        href, text = m.group(1).strip(), _clean(m.group(2))
        if not text or not _PROMOTER_TERMS.search(text):
            continue
        url = href if href.startswith("http") else f"{SEBON_BASE}{href}"
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Look at a 300-char window around the anchor for a date and ticker
        span = html[max(0, m.start() - 300): m.end() + 300]
        date_m = _DATE_NEAR_RE.search(span)
        notice_date = _normalize_date(date_m.group(1)) if date_m else None
        ticker_hint = _ticker_hint(text)

        out.append({
            "title": text,
            "url": url,
            "notice_date": notice_date,
            "symbol_hint": ticker_hint,
            "source": "sebon.gov.np",
            "kind": "promoter_prior_notice",
        })
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _normalize_date(raw: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# NEPSE-cache tickers — used as an allowlist so a random capitalized word
# in a title doesn't get promoted to `symbol`. Lazy loaded once.
_CACHED_SYMBOLS: set[str] | None = None


def _cached_symbols() -> set[str]:
    global _CACHED_SYMBOLS
    if _CACHED_SYMBOLS is not None:
        return _CACHED_SYMBOLS
    cache_dir = Path(__file__).resolve().parents[2] / "data" / "cache" / "nepse_ohlcv"
    _CACHED_SYMBOLS = {p.stem.upper() for p in cache_dir.glob("*.pkl")}
    return _CACHED_SYMBOLS


def _ticker_hint(title: str) -> str | None:
    """Return the first cached-universe ticker that appears in `title`, or None."""
    known = _cached_symbols()
    if not known:
        return None
    for m in _TICKER_HINT.finditer(title):
        candidate = m.group(1).upper()
        if candidate in known:
            return candidate
    return None


def ingest_sebon_promoter_notices(pages: int = 3) -> int:
    """Fetch the first `pages` of SEBON notices, persist candidate rows.

    Returns the number of NEW rows written today (dedupes across today's
    file). Non-fatal on any failure — returns 0 and logs.
    """
    today = date.today().isoformat()
    path = _out_path(today)
    new_rows = 0
    for pg in range(1, pages + 1):
        html = _fetch_notices_html(pg)
        if html is None:
            if pg == 1:
                logger.warning("SEBON notices unreachable — 0 rows this run")
                return 0
            break
        for row in _extract_candidates(html):
            key = (row.get("title"), row.get("notice_date"), row.get("url"))
            if _already_seen(path, key):
                continue
            row["fetched_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            new_rows += 1
    logger.info("SEBON promoter scrape: +%d new rows -> %s", new_rows, path)
    return new_rows


def load_promoter_notices(since_date: str | None = None) -> list[dict]:
    """Yield persisted promoter-prior-notice rows, optionally filtered by
    on-or-after `since_date` (matches on `notice_date` field, best-effort).
    """
    if not OUT_DIR.exists():
        return []
    rows: list[dict] = []
    for p in sorted(OUT_DIR.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_date and r.get("notice_date") and r["notice_date"] < since_date:
                continue
            rows.append(r)
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = ingest_sebon_promoter_notices()
    print(f"Ingested {n} new promoter-notice candidate row(s).")
