"""Broker reputation ledger (P21).

Every day, for every top-1 BUYING broker of a symbol, we record what
happened to that symbol's price 5 trading days later. Over months a
per-broker predictive edge emerges: broker 77 is right 62% of the time,
broker 22 is right 44%. When these brokers show up as today's top
buyers/sellers, the ensemble adjusts weight by their empirical edge.

This is the SINGLE biggest edge available on NEPSE public data — the
retail-heavy exchange has recognizable smart-money brokers whose
patterns are readable from the public floorsheet.

Storage: data/broker_reputation.json
    {
      "broker_id": {
        "buy": {"n": N, "hits_5d": H, "avg_next5d_return_pct": X},
        "sell": {"n": N, "hits_5d": H, "avg_next5d_return_pct": X}
      }
    }
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

REPUTATION_PATH = Path(__file__).resolve().parents[2] / "data" / "broker_reputation.json"
MIN_OBS_FOR_EDGE = 15    # need at least 15 events before we score a broker


@dataclass
class BrokerStats:
    n_buys: int = 0
    hits_buy_5d: int = 0
    sum_ret_buy_5d: float = 0.0
    n_sells: int = 0
    hits_sell_5d: int = 0
    sum_ret_sell_5d: float = 0.0

    @property
    def buy_hit_rate(self) -> float:
        return self.hits_buy_5d / self.n_buys if self.n_buys else 0.5

    @property
    def sell_hit_rate(self) -> float:
        return self.hits_sell_5d / self.n_sells if self.n_sells else 0.5

    @property
    def avg_ret_after_buy_5d(self) -> float:
        return self.sum_ret_buy_5d / self.n_buys if self.n_buys else 0.0

    @property
    def avg_ret_after_sell_5d(self) -> float:
        return self.sum_ret_sell_5d / self.n_sells if self.n_sells else 0.0

    @property
    def edge_score(self) -> float:
        """Signed edge in [-1, +1]. Positive = smart-money buyer;
        negative = accumulates before drops (avoid following)."""
        # Weight by observation count so few-obs brokers don't dominate
        n = self.n_buys + self.n_sells
        if n < MIN_OBS_FOR_EDGE:
            return 0.0
        buy_edge = (self.buy_hit_rate - 0.5) * 2       # -1..+1
        sell_edge = -(self.sell_hit_rate - 0.5) * 2    # inverted: right when they sell = bearish
        edge = 0.6 * buy_edge + 0.4 * sell_edge
        return round(max(-1.0, min(1.0, edge)), 3)


def _load_ledger() -> dict[str, BrokerStats]:
    if not REPUTATION_PATH.exists():
        return {}
    try:
        raw = json.loads(REPUTATION_PATH.read_text())
    except Exception:
        return {}
    out = {}
    for broker, s in (raw.get("brokers") or {}).items():
        out[broker] = BrokerStats(**s)
    return out


def _save_ledger(ledger: dict[str, BrokerStats]) -> None:
    REPUTATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPUTATION_PATH.write_text(json.dumps({
        "brokers": {b: {"n_buys": s.n_buys, "hits_buy_5d": s.hits_buy_5d,
                        "sum_ret_buy_5d": round(s.sum_ret_buy_5d, 4),
                        "n_sells": s.n_sells, "hits_sell_5d": s.hits_sell_5d,
                        "sum_ret_sell_5d": round(s.sum_ret_sell_5d, 4)}
                     for b, s in ledger.items()},
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }, indent=2))


def update_reputation_ledger(price_provider, lookforward_days: int = 5) -> dict:
    """Walk historical daily floorsheet files, join top-buyer/top-seller
    per symbol per day with the 5-day forward return. Update the ledger.

    Idempotent-ish: rebuilds from scratch each run (fast; 51k rows/day
    aggregate to ~340 symbol-day records).
    """
    from argos.data.floorsheet import FLOORSHEET_DIR
    ledger: dict[str, BrokerStats] = defaultdict(BrokerStats)
    total_events = 0

    # Cache OHLCV per symbol so we don't refetch
    price_cache: dict[str, dict[str, float]] = {}

    def _get_close(sym: str, d: str) -> float | None:
        if sym not in price_cache:
            try:
                data = price_provider(sym, d, lookback_days=400)
                price_cache[sym] = {r["date"]: r["close"] for r in data["ohlcv"]}
            except Exception:
                price_cache[sym] = {}
        return price_cache[sym].get(d)

    def _next_close_date(sym: str, base_date: str, offset: int) -> tuple[str, float] | None:
        prices = price_cache.get(sym) or {}
        if not prices:
            return None
        sorted_dates = sorted(prices.keys())
        if base_date not in sorted_dates:
            return None
        idx = sorted_dates.index(base_date)
        if idx + offset >= len(sorted_dates):
            return None
        d = sorted_dates[idx + offset]
        return (d, prices[d])

    if not FLOORSHEET_DIR.exists():
        logger.warning("No floorsheet directory yet — cannot build reputation")
        return {}

    for jf in sorted(FLOORSHEET_DIR.glob("*.jsonl")):
        day = jf.stem
        # Group trades by symbol
        by_symbol: dict[str, list[dict]] = defaultdict(list)
        for line in jf.read_text().splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    by_symbol[r["symbol"].upper()].append(r)
                except Exception:
                    continue

        for sym, trades in by_symbol.items():
            # Prime price cache + get 5-day forward return
            _get_close(sym, day)
            fwd = _next_close_date(sym, day, lookforward_days)
            entry_close = _get_close(sym, day)
            if not fwd or not entry_close:
                continue
            _, fwd_close = fwd
            ret_pct = (fwd_close / entry_close - 1) * 100

            # Top-buyer (by quantity) and top-seller
            buyer_vol = Counter()
            seller_vol = Counter()
            for tr in trades:
                buyer_vol[tr["buyer_broker_id"]] += tr.get("quantity", 0)
                seller_vol[tr["seller_broker_id"]] += tr.get("quantity", 0)
            top_buyer = buyer_vol.most_common(1)[0][0] if buyer_vol else None
            top_seller = seller_vol.most_common(1)[0][0] if seller_vol else None
            # Update
            if top_buyer:
                st = ledger[top_buyer]
                st.n_buys += 1
                st.sum_ret_buy_5d += ret_pct
                if ret_pct > 0:
                    st.hits_buy_5d += 1
                total_events += 1
            if top_seller and top_seller != top_buyer:
                st = ledger[top_seller]
                st.n_sells += 1
                st.sum_ret_sell_5d += ret_pct
                if ret_pct < 0:
                    st.hits_sell_5d += 1

    _save_ledger(ledger)
    logger.info("Broker reputation: %d brokers tracked from %d symbol-day events",
                len(ledger), total_events)
    return ledger


def broker_edge_score(broker_id: str) -> float:
    """Return signed edge score in [-1, +1] for a specific broker.

    0.0 = neutral / insufficient data.
    """
    ledger = _load_ledger()
    st = ledger.get(broker_id)
    if not st:
        return 0.0
    return st.edge_score


def smart_broker_context(symbol: str, d: str) -> dict:
    """For today's top-buyer/top-seller of a symbol, return their edge scores.

    Used by the ensemble to boost/fade the smart_money signal based on
    WHO is doing the buying/selling.
    """
    from argos.data.floorsheet import daily_symbol_scores
    ds = daily_symbol_scores(d, symbol)
    if not ds:
        return {}
    tb = ds.get("top_buyer", {}).get("broker")
    ts = ds.get("top_seller", {}).get("broker")
    return {
        "top_buyer_id": tb,
        "top_buyer_edge": broker_edge_score(tb) if tb else 0.0,
        "top_seller_id": ts,
        "top_seller_edge": broker_edge_score(ts) if ts else 0.0,
    }
