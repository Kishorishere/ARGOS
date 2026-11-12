"""EDGAR client — SEC filings, no API key.

Covers what the Ownership panel needs in Phase C.v1:

  * `ticker_to_cik(ticker)` — SEC's ticker ↔ CIK map, cached on disk for
    a day. Required for every other call.
  * `recent_form4s(ticker, limit)` — insider transactions. Parses the
    Form 4 XML defensively and returns one structured row per non-
    derivative transaction (common-stock buys/sells).

NOT covered in v1 (intentionally — these are more expensive and belong
to a separate Phase C.2 PR):

  * 13F holdings deltas — requires either iterating N filers' quarterly
    informationTable.xml dumps, or hitting EDGAR's full-text search,
    both much more involved than Form 4. Phase C.2 ships a cached
    "top 50 institutional holders" map built by scraping 13Fs once a
    week offline, then the Ownership panel reads from that cache rather
    than hitting EDGAR every pageview.

EDGAR rules:
  * Must send a descriptive User-Agent with contact info.
  * Rate limit: 10 req/s per IP; we cache aggressively and never burst.

Config:
  * `EDGAR_USER_AGENT` env var sets the User-Agent string. If unset we
    fall back to a generic placeholder; replace it in .env before
    running against production EDGAR.
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
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data" / "cache" / "edgar"

DEFAULT_UA = "Argos Research Desk research@argos.local"
BASE_SEC = "https://www.sec.gov"
BASE_DATA = "https://data.sec.gov"
TICKERS_URL = f"{BASE_SEC}/files/company_tickers.json"

# TTLs
TICKER_MAP_TTL_S = 24 * 60 * 60                # one day
SUBMISSIONS_TTL_S = 6 * 60 * 60                # six hours — fresh Form 4s matter
FILING_DOC_TTL_S = 30 * 24 * 60 * 60           # thirty days — filings are immutable

_TICKER_MAP_CACHE: dict[str, dict] | None = None


def _ua() -> str:
    return os.environ.get("EDGAR_USER_AGENT") or DEFAULT_UA


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": _ua(),
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",   # will be overridden per request
    })
    return s


def _get(url: str, host: str = "www.sec.gov") -> requests.Response:
    s = _session()
    s.headers["Host"] = host
    # Soft throttle — 100ms between requests keeps us well under 10/s.
    time.sleep(0.1)
    r = s.get(url, timeout=20)
    r.raise_for_status()
    return r


# ── Disk cache ─────────────────────────────────────────────────────────────

def _cache_read_json(path: Path, ttl_s: int) -> dict | None:
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > ttl_s:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _cache_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as e:
        logger.debug("edgar cache write failed %s: %s", path, e)


def _cache_read_text(path: Path, ttl_s: int) -> str | None:
    if not path.exists():
        return None
    try:
        if time.time() - path.stat().st_mtime > ttl_s:
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _cache_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        logger.debug("edgar cache write failed %s: %s", path, e)


# ── Ticker → CIK ────────────────────────────────────────────────────────────

def _load_ticker_map() -> dict[str, dict]:
    global _TICKER_MAP_CACHE
    if _TICKER_MAP_CACHE is not None:
        return _TICKER_MAP_CACHE
    path = CACHE_DIR / "company_tickers.json"
    data = _cache_read_json(path, TICKER_MAP_TTL_S)
    if data is None:
        logger.info("edgar: fetching ticker map")
        r = _get(TICKERS_URL, host="www.sec.gov")
        data = r.json()
        _cache_write_json(path, data)
    # file shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}, ...}
    out = {}
    for row in data.values():
        t = str(row.get("ticker") or "").upper()
        if not t:
            continue
        out[t] = {
            "cik": int(row["cik_str"]),
            "name": row.get("title"),
        }
    _TICKER_MAP_CACHE = out
    return out


def ticker_to_cik(ticker: str) -> Optional[int]:
    """Return the integer CIK for a ticker, or None if unknown.
    Only US-listed issuers with an SEC filing history are in the map;
    ADRs and OTC names can miss."""
    try:
        return _load_ticker_map().get(ticker.upper(), {}).get("cik")
    except Exception as e:  # noqa: BLE001
        logger.warning("edgar: ticker_to_cik(%s) failed: %s", ticker, e)
        return None


# ── Submissions (recent filings) ────────────────────────────────────────────

def _submissions(cik: int) -> Optional[dict]:
    path = CACHE_DIR / "submissions" / f"CIK{cik:010d}.json"
    data = _cache_read_json(path, SUBMISSIONS_TTL_S)
    if data is not None:
        return data
    url = f"{BASE_DATA}/submissions/CIK{cik:010d}.json"
    try:
        r = _get(url, host="data.sec.gov")
        data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("edgar: submissions(CIK=%d) failed: %s", cik, e)
        return None
    _cache_write_json(path, data)
    return data


# ── Form 4 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Form4Event:
    ticker: str
    filed_at: str                  # "YYYY-MM-DD"
    period_of_report: Optional[str]
    reporting_owner: Optional[str]
    owner_title: Optional[str]
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    transaction_date: Optional[str]
    transaction_shares: Optional[float]
    transaction_price: Optional[float]
    direction: Optional[str]       # "A" (acquired) | "D" (disposed) | None
    security_title: Optional[str]
    accession: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


def _form4_doc_url(cik: int, accession_no: str) -> str:
    """The Form 4 filing index lists documents; we want the primary XML.
    Convention: `primary_doc.xml` lives in the filing's accession folder.
    Accession numbers come with dashes in submissions.json; folders use
    the no-dash variant."""
    folder = accession_no.replace("-", "")
    return f"{BASE_SEC}/Archives/edgar/data/{cik}/{folder}/primary_doc.xml"


def _findtext(node, path) -> Optional[str]:
    """ET.findtext but strips namespaces before matching — EDGAR XML uses
    inconsistent namespacing. We walk once to strip ns, then path-find."""
    if node is None:
        return None
    el = node.find(path)
    if el is not None and el.text:
        return el.text.strip() or None
    return None


def _strip_ns(root: ET.Element) -> ET.Element:
    """Drop XML namespaces so paths like .//reportingOwner always match."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _parse_form4_xml(xml: str, ticker: str, accession: str, filed_at: str, url: str) -> list[Form4Event]:
    try:
        root = _strip_ns(ET.fromstring(xml))
    except Exception as e:  # noqa: BLE001
        logger.debug("edgar: Form4 XML parse failed on %s: %s", accession, e)
        return []

    period = _findtext(root, ".//periodOfReport")
    owner_name = _findtext(root, ".//reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    is_dir = (_findtext(rel, "isDirector") or "0").strip() in ("1", "true", "True")
    is_off = (_findtext(rel, "isOfficer") or "0").strip() in ("1", "true", "True")
    is_ten = (_findtext(rel, "isTenPercentOwner") or "0").strip() in ("1", "true", "True")
    title = _findtext(rel, "officerTitle")

    out: list[Form4Event] = []

    for tx in root.findall(".//nonDerivativeTable/nonDerivativeTransaction"):
        shares_text = _findtext(tx, "transactionAmounts/transactionShares/value")
        price_text = _findtext(tx, "transactionAmounts/transactionPricePerShare/value")
        direction = _findtext(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")
        date = _findtext(tx, "transactionDate/value")
        sec_title = _findtext(tx, "securityTitle/value")
        try:
            shares = float(shares_text) if shares_text else None
        except ValueError:
            shares = None
        try:
            price = float(price_text) if price_text else None
        except ValueError:
            price = None
        out.append(Form4Event(
            ticker=ticker.upper(), filed_at=filed_at, period_of_report=period,
            reporting_owner=owner_name, owner_title=title,
            is_director=is_dir, is_officer=is_off, is_ten_percent_owner=is_ten,
            transaction_date=date, transaction_shares=shares, transaction_price=price,
            direction=direction, security_title=sec_title,
            accession=accession, url=url,
        ))
    return out


def recent_form4s(ticker: str, limit: int = 10, max_fetch: int = 20) -> list[Form4Event]:
    """Return the latest `limit` non-derivative Form 4 transactions for
    the ticker's issuer. Fetches up to `max_fetch` Form 4 filings
    (each may contain multiple transactions). Empty on any failure.

    Note: Form 4 is filed *by the insider* about *this issuer* — the
    SEC submissions.json index is keyed on the issuer's CIK and lists
    all Form 4s referencing it, so one API call gives us the full list
    of insider filings; we then fetch each filing's XML.
    """
    cik = ticker_to_cik(ticker)
    if cik is None:
        return []
    sub = _submissions(cik)
    if not sub:
        return []

    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accs = recent.get("accessionNumber") or []
    if not forms:
        return []

    picks = [(forms[i], dates[i], accs[i]) for i in range(len(forms))
             if forms[i] == "4"][:max_fetch]
    if not picks:
        return []

    events: list[Form4Event] = []
    for _form, filed_at, accession in picks:
        url = _form4_doc_url(cik, accession)
        cache_path = CACHE_DIR / "form4" / f"{cik}_{accession.replace('-', '')}.xml"
        xml = _cache_read_text(cache_path, FILING_DOC_TTL_S)
        if xml is None:
            try:
                xml = _get(url, host="www.sec.gov").text
                _cache_write_text(cache_path, xml)
            except Exception as e:  # noqa: BLE001
                logger.debug("edgar: fetch Form4 %s/%s failed: %s", cik, accession, e)
                continue
        events.extend(_parse_form4_xml(xml, ticker, accession, filed_at, url))
        if len(events) >= limit:
            break
    return events[:limit]


# ── Institutional holders (Phase C.2 placeholder) ──────────────────────────

@dataclass(frozen=True)
class InstitutionalHolder:
    ticker: str
    filer_name: str
    cik: Optional[int]
    shares: Optional[int]
    value_usd: Optional[float]
    as_of: Optional[str]
    source_url: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


HOLDERS_CACHE_TTL_S = 24 * 60 * 60     # 13Fs are quarterly; 24h cache is generous


def _holders_cache_path(ticker: str) -> Path:
    return CACHE_DIR / "holders" / f"{ticker.upper()}.json"


def _read_cached_holders(ticker: str) -> Optional[list[dict]]:
    data = _cache_read_json(_holders_cache_path(ticker), HOLDERS_CACHE_TTL_S)
    if data is None:
        return None
    return data if isinstance(data, list) else None


def _write_cached_holders(ticker: str, rows: list[dict]) -> None:
    _cache_write_json(_holders_cache_path(ticker), rows)


def _fetch_yf_holders(ticker: str) -> list[dict]:
    """Phase C.2 primary source: yfinance's aggregated institutional-
    holders table. This isn't the raw 13F dump (that lives in
    informationTable.xml and requires per-filer iteration), but it's the
    pragmatic fit — yfinance updates from the same quarterly 13F cycle
    and is the industry default for "who holds X" without a paid feed.

    Columns we actually read: Holder, Shares, Date Reported, % Out,
    Value. Columns yfinance has historically renamed sometimes, so we
    pull them case-insensitively and fall back to [].
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        df = t.institutional_holders
    except Exception as e:  # noqa: BLE001
        logger.debug("yfinance institutional_holders(%s) failed: %s", ticker, e)
        return []
    if df is None:
        return []
    try:
        if hasattr(df, "empty") and df.empty:
            return []
    except Exception:  # noqa: BLE001
        pass
    # Normalize column names to lowercase for robustness.
    try:
        cols = {c: c.lower() for c in df.columns}
        df2 = df.rename(columns=cols)
    except Exception:  # noqa: BLE001
        return []

    def _col(df_, *names):
        for n in names:
            if n in df_.columns:
                return df_[n]
        return None

    holder = _col(df2, "holder")
    shares = _col(df2, "shares")
    date_reported = _col(df2, "date reported", "date_reported", "datereported")
    pct = _col(df2, "% out", "pctheld", "percent_out", "percentageheld", "% held")
    value = _col(df2, "value")

    rows: list[dict] = []
    n_rows = len(df2.index)
    for i in range(n_rows):
        def _cell(col, cast=float):
            if col is None:
                return None
            try:
                v = col.iloc[i]
                if v is None:
                    return None
                return cast(v)
            except Exception:  # noqa: BLE001
                return None

        filer = None
        if holder is not None:
            try:
                filer = str(holder.iloc[i])
            except Exception:  # noqa: BLE001
                filer = None
        as_of = None
        if date_reported is not None:
            try:
                raw_dt = date_reported.iloc[i]
                as_of = str(raw_dt)[:10]
            except Exception:  # noqa: BLE001
                as_of = None
        rows.append({
            "ticker": ticker.upper(),
            "filer_name": filer or "unknown",
            "cik": None,                    # yfinance doesn't expose CIK
            "shares": _cell(shares, lambda v: int(float(v))),
            "value_usd": _cell(value, float),
            "pct_outstanding": _cell(pct, float),
            "as_of": as_of,
            "source_url": None,
        })
    return rows


def top_institutional_holders(ticker: str, limit: int = 10) -> list[InstitutionalHolder]:
    """Phase C.2: 13F-derived top institutional holders via yfinance.

    Returns up to `limit` rows sorted by reported shares (descending).
    Cached on disk for 24h. Empty list + no exception if the ticker
    isn't covered by yfinance's holders feed (small caps, new listings).
    """
    rows = _read_cached_holders(ticker)
    if rows is None:
        rows = _fetch_yf_holders(ticker)
        _write_cached_holders(ticker, rows)
    rows.sort(key=lambda r: r.get("shares") or 0, reverse=True)
    out: list[InstitutionalHolder] = []
    for r in rows[:limit]:
        out.append(InstitutionalHolder(
            ticker=r["ticker"],
            filer_name=r.get("filer_name") or "unknown",
            cik=r.get("cik"),
            shares=r.get("shares"),
            value_usd=r.get("value_usd"),
            as_of=r.get("as_of"),
            source_url=r.get("source_url"),
        ))
    return out


__all__ = [
    "Form4Event",
    "InstitutionalHolder",
    "recent_form4s",
    "top_institutional_holders",
    "ticker_to_cik",
]
