"""Cross-symbol broker pattern detection (P26).

Reads the last N days of floorsheet files and asks: is any single broker
persistently accumulating (or distributing) MULTIPLE names — and are
those names clustered in one sector? A broker showing up as top-buyer of
NABIL + ADBL + EBL in the same week is a sector-wide institutional
entry signal that no single-symbol view can see.

Outputs:
  - `top_buyer_streak(symbol, days=5)`   — is today's #1 buyer also
    the #1 buyer over the trailing window? (persistence per-symbol)
  - `sector_broker_signal(sector, days=5)` — {broker_id: n_symbols_led}
    the top brokers by symbol-count in one sector.
  - `cross_symbol_broker_edge(symbol, d, window_days=5)` — signed [-1, +1]
    edge combining sector-wide cross-symbol persistence AND that broker's
    empirical hit-rate from broker_reputation.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

FLOORSHEET_DIR = Path(__file__).resolve().parents[2] / "data" / "floorsheet"


def _load_day_rows(d: str) -> list[dict]:
    p = FLOORSHEET_DIR / f"{d}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _daily_top_broker_by_symbol(d: str) -> dict[str, dict]:
    """{symbol: {"top_buyer": id, "top_seller": id, "buy_vol": v, "sell_vol": v}}."""
    by_symbol: dict[str, dict] = defaultdict(lambda: {"buy": Counter(), "sell": Counter()})
    for r in _load_day_rows(d):
        sym = (r.get("symbol") or "").upper()
        if not sym:
            continue
        q = int(r.get("quantity", 0))
        by_symbol[sym]["buy"][r.get("buyer_broker_id")] += q
        by_symbol[sym]["sell"][r.get("seller_broker_id")] += q
    out: dict[str, dict] = {}
    for sym, sides in by_symbol.items():
        tb = sides["buy"].most_common(1)
        ts = sides["sell"].most_common(1)
        out[sym] = {
            "top_buyer": tb[0][0] if tb else None,
            "buy_vol": tb[0][1] if tb else 0,
            "top_seller": ts[0][0] if ts else None,
            "sell_vol": ts[0][1] if ts else 0,
        }
    return out


def _recent_days(as_of: str, n: int) -> list[str]:
    d0 = datetime.strptime(as_of, "%Y-%m-%d").date()
    return [(d0 - timedelta(days=i)).isoformat() for i in range(n)]


def top_buyer_streak(symbol: str, as_of: str, days: int = 5,
                     min_sessions: int = 3) -> dict:
    """Is the same broker the #1 buyer for `days` sessions in a row?

    Returns {"broker": id, "streak_days": N, "signal": [-1,+1]} where +1
    means a single broker has been top-buyer for all `days` sessions,
    -1 the same but on the sell side. Signal = 0.0 when fewer than
    `min_sessions` non-empty days are available (avoids false spikes
    from a single day of thin data).
    """
    seq_buy: list[str] = []
    seq_sell: list[str] = []
    for d in _recent_days(as_of, days):
        tops = _daily_top_broker_by_symbol(d).get(symbol.upper())
        if not tops:
            continue
        if tops["top_buyer"]:
            seq_buy.append(tops["top_buyer"])
        if tops["top_seller"]:
            seq_sell.append(tops["top_seller"])
    if len(seq_buy) < min_sessions:
        return {"signal": 0.0, "sessions_seen": len(seq_buy)}
    # Buyer persistence
    buy_top = Counter(seq_buy).most_common(1)[0]
    sell_top = Counter(seq_sell).most_common(1)[0] if seq_sell else (None, 0)
    persistence_buy = buy_top[1] / len(seq_buy)  # 0..1
    persistence_sell = (sell_top[1] / len(seq_sell)) if seq_sell else 0.0
    # Only fire if we see hard dominance (>= 60% of window)
    if persistence_buy >= 0.6 and persistence_buy >= persistence_sell:
        return {"broker": buy_top[0], "streak_days": buy_top[1],
                "side": "buy", "signal": round(persistence_buy * 2 - 1, 3)}
    if persistence_sell >= 0.6 and persistence_sell > persistence_buy:
        return {"broker": sell_top[0], "streak_days": sell_top[1],
                "side": "sell", "signal": round(-(persistence_sell * 2 - 1), 3)}
    return {"signal": 0.0}


def sector_broker_signal(sector: str, as_of: str, days: int = 5) -> dict:
    """Which brokers led buying/selling across MULTIPLE symbols in this sector
    over the last `days`?

    Returns:
      {
        "buy_leaders":  [(broker_id, n_symbol_days_led)],
        "sell_leaders": [...],
        "cross_symbol_score": float in [-1,+1]
      }
    """
    try:
        from argos.data.nepse_sectors import symbols_in_sector
        sector_symbols = {s.upper() for s in symbols_in_sector(sector)}
    except Exception:
        sector_symbols = set()
    if not sector_symbols:
        return {"cross_symbol_score": 0.0}

    buy_leader_count = Counter()   # {broker: total (symbol×day) leadership hits}
    sell_leader_count = Counter()
    total_slots = 0

    for d in _recent_days(as_of, days):
        for sym, tops in _daily_top_broker_by_symbol(d).items():
            if sym not in sector_symbols:
                continue
            total_slots += 1
            if tops["top_buyer"]:
                buy_leader_count[tops["top_buyer"]] += 1
            if tops["top_seller"]:
                sell_leader_count[tops["top_seller"]] += 1

    # Need at least ~1 avg day of coverage across the sector, else signal
    # is dominated by a single day's noise
    if total_slots < max(3, len(sector_symbols) // 3):
        return {"cross_symbol_score": 0.0, "sector_slots": total_slots}

    top_buy = buy_leader_count.most_common(3)
    top_sell = sell_leader_count.most_common(3)
    # Score: dominant buy broker's share − dominant sell broker's share
    top_buy_share = (top_buy[0][1] / total_slots) if top_buy else 0
    top_sell_share = (top_sell[0][1] / total_slots) if top_sell else 0
    score = top_buy_share - top_sell_share
    return {
        "buy_leaders": top_buy,
        "sell_leaders": top_sell,
        "sector_slots": total_slots,
        "cross_symbol_score": round(max(-1.0, min(1.0, score * 2)), 3),
    }


def cross_symbol_broker_edge(symbol: str, as_of: str, window_days: int = 5) -> float | None:
    """Signed [-1, +1] combining:
      - persistence: is one broker consistently #1 buyer/seller of THIS symbol?
      - sector-wide: is that broker also dominating other names in same sector?
      - broker reputation: is that broker historically right?

    +1.0 = a smart-money broker persistently accumulating this symbol
           AND also accumulating peers in the sector.
    -1.0 = same but on the sell side.
    """
    streak = top_buyer_streak(symbol, as_of, days=window_days)
    if not streak.get("signal"):
        return 0.0
    streak_signal = streak["signal"]           # -1..+1
    broker = streak.get("broker")

    # Sector amplifier
    sector_score = 0.0
    try:
        from argos.data.nepse_sectors import symbol_to_sector
        sector = symbol_to_sector(symbol)
        if sector:
            sect = sector_broker_signal(sector, as_of, days=window_days)
            buy_leaders = sect.get("buy_leaders") or []
            sell_leaders = sect.get("sell_leaders") or []
            # Sector amplifier: if this same broker also leads sector, magnify
            if broker in {b for b, _ in buy_leaders}:
                sector_score = 0.4
            elif broker in {b for b, _ in sell_leaders}:
                sector_score = -0.4
    except Exception:
        pass

    # Reputation amplifier
    rep_amp = 0.0
    try:
        from argos.data.broker_reputation import broker_edge_score
        rep = broker_edge_score(broker) if broker else 0.0
        rep_amp = rep * 0.3                    # small nudge from historical accuracy
    except Exception:
        pass

    combined = 0.6 * streak_signal + sector_score + rep_amp
    return round(max(-1.0, min(1.0, combined)), 3)
