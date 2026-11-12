"""Raw-data snapshot service — no LLM calls.

Collects the per-ticker evidence a trader (and the LLM agents) would reason
over: price, computed indicators, fundamentals, portfolio exposure, data
freshness. Every section is best-effort; a missing data source returns a
section with an `error` string rather than raising, so the UI can degrade
gracefully instead of blanking out.

This is Phase 0 plumbing — no agents run here. Call `/api/analyze/{ticker}`
when you want the committee to reason over the same substrate.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ── Price ───────────────────────────────────────────────────────────────────

def _price_block(ticker: str, as_of: str, live: bool) -> dict:
    try:
        from argos.data.ohlcv_loader import load_ohlcv
        window = load_ohlcv(ticker, as_of, allow_today=live)
    except Exception as e:  # noqa: BLE001
        return {"error": f"ohlcv_load_failed: {e}"}

    bars = window.get("ohlcv") or []
    if not bars:
        return {"error": "no_bars"}
    last = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else None
    change = (last["close"] - prev["close"]) if prev else 0.0
    pct = (change / prev["close"] * 100.0) if prev and prev["close"] else 0.0
    return {
        "last": last["close"],
        "open": last["open"],
        "high": last["high"],
        "low": last["low"],
        "volume": last["volume"],
        "prev_close": prev["close"] if prev else None,
        "change": round(change, 4),
        "change_pct": round(pct, 4),
        "last_bar_date": last["date"],
        "n_bars": len(bars),
    }


# ── Indicators ──────────────────────────────────────────────────────────────

def _indicators_block(ticker: str, as_of: str, live: bool) -> dict:
    try:
        from argos.data.ohlcv_loader import load_ohlcv
        from argos.indicators.engine import compute_snapshot
        window = load_ohlcv(ticker, as_of, allow_today=live)
        snap = compute_snapshot(window.get("ohlcv") or [], ticker, as_of)
    except Exception as e:  # noqa: BLE001
        return {"error": f"indicator_compute_failed: {e}"}

    # IndicatorSnapshot is a pydantic model in argos.indicators.schemas;
    # fall back to best-effort dict conversion so this service never
    # couples to the exact schema class.
    to_dict = getattr(snap, "model_dump", None) or getattr(snap, "dict", None)
    return to_dict() if callable(to_dict) else dict(snap)


# ── Fundamentals ────────────────────────────────────────────────────────────

def _fundamentals_block(ticker: str) -> dict:
    """Best-effort fundamentals lookup. The store is currently NEPSE-oriented
    (`data/fundamentals/{SYMBOL}.jsonl`), so NASDAQ tickers may miss. That's
    OK — return a structured error and the UI shows "no cached fundamentals."
    Phase C adds a NASDAQ fundamentals source.
    """
    path = REPO_ROOT / "data" / "fundamentals" / f"{ticker.upper()}.jsonl"
    if not path.exists():
        return {"error": f"no_cached_fundamentals: {ticker}"}
    try:
        import json
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not rows:
            return {"error": "empty_fundamentals_file"}
        latest = rows[-1]
        return {
            "latest_quarter": latest.get("quarter"),
            "pe": latest.get("p_e_ratio"),
            "pb": latest.get("p_b_ratio"),
            "roe_pct": latest.get("roe_pct"),
            "roa_pct": latest.get("roa_pct"),
            "eps": latest.get("eps_npr") or latest.get("eps"),
            "book_value": latest.get("book_value_npr") or latest.get("book_value"),
            "revenue": latest.get("revenue_npr") or latest.get("revenue"),
            "net_profit": latest.get("net_profit_npr") or latest.get("net_profit"),
            "n_quarters_available": len(rows),
            "source_path": str(path.relative_to(REPO_ROOT)),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"fundamentals_read_failed: {e}"}


# ── Portfolio exposure for this ticker ──────────────────────────────────────

def _portfolio_block(ticker: str) -> dict:
    try:
        from argos.portfolio import load_portfolio
    except Exception as e:  # noqa: BLE001
        return {"error": f"portfolio_import_failed: {e}"}
    try:
        pf = load_portfolio()
    except Exception as e:  # noqa: BLE001
        return {"error": f"portfolio_load_failed: {e}"}

    positions = getattr(pf, "positions", []) or []
    held = None
    for p in positions:
        try:
            if getattr(p, "ticker", "").upper() == ticker.upper():
                mv = getattr(p, "market_value", None)
                try:
                    mv = float(mv() if callable(mv) else mv)
                except Exception:  # noqa: BLE001
                    mv = None
                held = {
                    "ticker": p.ticker,
                    "sector": getattr(p, "sector", "Unknown"),
                    "quantity": getattr(p, "quantity", 0.0),
                    "avg_price": getattr(p, "avg_price", 0.0),
                    "market_price": getattr(p, "market_price", 0.0),
                    "market_value": mv,
                }
                break
        except Exception:  # noqa: BLE001
            continue

    # High-level portfolio context (safe if to_risk_context is missing)
    risk_ctx: dict | None = None
    try:
        tc = getattr(pf, "to_risk_context", None)
        if callable(tc):
            risk_ctx = tc()
    except Exception as e:  # noqa: BLE001
        risk_ctx = {"error": f"risk_context_failed: {e}"}

    return {
        "position": held,            # None if not held
        "held": held is not None,
        "portfolio_context": risk_ctx,
    }


# ── Freshness ───────────────────────────────────────────────────────────────

def _freshness_block(as_of: str, price_block: dict, fundamentals_block: dict) -> dict:
    out: dict[str, Any] = {"as_of": as_of, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    last_bar = price_block.get("last_bar_date") if isinstance(price_block, dict) else None
    if last_bar:
        try:
            lb = datetime.strptime(last_bar, "%Y-%m-%d")
            today = datetime.now()
            out["ohlcv_age_days"] = (today - lb).days
            out["ohlcv_last_bar"] = last_bar
        except Exception:  # noqa: BLE001
            pass
    q = fundamentals_block.get("latest_quarter") if isinstance(fundamentals_block, dict) else None
    if q:
        out["fundamentals_latest_quarter"] = q
    return out


# ── Public API ──────────────────────────────────────────────────────────────

def build_snapshot(ticker: str, as_of: str | None = None) -> dict:
    """Assemble a raw-data snapshot for one ticker. No LLM calls.

    `as_of` defaults to today. When as_of == today, the OHLCV loader is
    invoked in live mode (partial last bar allowed). Historical as_of
    values route through the backtest path (strictly past data).
    """
    ticker = ticker.upper()
    as_of = as_of or _today_iso()
    live = (as_of == _today_iso())

    t0 = time.time()
    price = _price_block(ticker, as_of, live)
    indicators = _indicators_block(ticker, as_of, live)
    fundamentals = _fundamentals_block(ticker)
    portfolio = _portfolio_block(ticker)
    freshness = _freshness_block(as_of, price, fundamentals)

    errors = [
        block.get("error")
        for block in (price, indicators, fundamentals, portfolio)
        if isinstance(block, dict) and block.get("error")
    ]

    return {
        "ticker": ticker,
        "as_of": as_of,
        "live": live,
        "price": price,
        "indicators": indicators,
        "fundamentals": fundamentals,
        "portfolio": portfolio,
        "freshness": freshness,
        "errors": errors,
        "duration_s": round(time.time() - t0, 3),
    }
