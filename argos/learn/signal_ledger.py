"""Signal accuracy ledger (P12).

Every night — or before every backtest — this module walks the decision
journal, joins each fired indicator signal with the ACTUAL next-1-day
and next-5-day price move, and updates a rolling per-signal hit rate.

The composite scoring downstream (engine.py, forecast.py) reads this
ledger via `get_weight_multiplier(indicator_name)` to auto-tune weights:
signals with >60% hit rate get 1.5×; signals with <45% hit rate over
60+ observations get 0× (silenced). This is Argos learning from its own
mistakes with no human tuning.

Ledger format: a single JSON file at data/signal_ledger.json:
    {
      "signals": {
        "RSI(14)":         {"n": 214, "hits_1d": 118, "hits_5d": 132, "last_seen": "2024-12-15"},
        "MACD hist flip":  {"n":  87, "hits_1d":  52, "hits_5d":  61, "last_seen": ...},
        ...
      },
      "generated_at": "2024-12-16T09:00:00Z"
    }

Hit definition: signal.direction == 'bullish' AND next_return > 0 → hit.
                signal.direction == 'bearish' AND next_return < 0 → hit.
                'neutral' signals aren't counted (no bet).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = Path(os.environ.get("ARGOS_SIGNAL_LEDGER",
                                           str(Path(__file__).resolve().parents[2] / "data" / "signal_ledger.json")))
MIN_OBS_FOR_WEIGHT = 30    # need at least 30 firings before we trust the hit rate
UPPER_WEIGHT = 1.5         # >60% hit rate → 1.5×
LOWER_WEIGHT = 0.0         # <45% hit rate over 60+ obs → silenced


@dataclass
class SignalStats:
    n: int = 0
    hits_1d: int = 0
    hits_5d: int = 0
    last_seen: str = ""

    @property
    def hit_rate_1d(self) -> float:
        return self.hits_1d / self.n if self.n else 0.0

    @property
    def hit_rate_5d(self) -> float:
        return self.hits_5d / self.n if self.n else 0.0


@dataclass
class SignalLedger:
    signals: dict[str, SignalStats] = field(default_factory=dict)
    generated_at: str = ""

    def to_json(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "signals": {
                name: {"n": s.n, "hits_1d": s.hits_1d, "hits_5d": s.hits_5d,
                       "last_seen": s.last_seen,
                       "hit_rate_1d": round(s.hit_rate_1d, 3),
                       "hit_rate_5d": round(s.hit_rate_5d, 3)}
                for name, s in self.signals.items()
            },
        }

    @classmethod
    def from_json(cls, data: dict) -> "SignalLedger":
        signals = {}
        for name, d in (data.get("signals") or {}).items():
            signals[name] = SignalStats(n=d.get("n", 0),
                                        hits_1d=d.get("hits_1d", 0),
                                        hits_5d=d.get("hits_5d", 0),
                                        last_seen=d.get("last_seen", ""))
        return cls(signals=signals, generated_at=data.get("generated_at", ""))


def load_ledger(path: Path | None = None) -> SignalLedger:
    p = path or DEFAULT_LEDGER_PATH
    if not p.exists():
        return SignalLedger()
    try:
        return SignalLedger.from_json(json.loads(p.read_text()))
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read ledger %s: %s — starting fresh", p, e)
        return SignalLedger()


def save_ledger(ledger: SignalLedger, path: Path | None = None) -> None:
    p = path or DEFAULT_LEDGER_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    ledger.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    p.write_text(json.dumps(ledger.to_json(), indent=2))
    logger.info("Ledger saved to %s (%d signals tracked)", p, len(ledger.signals))


def get_weight_multiplier(indicator_name: str, ledger: SignalLedger | None = None) -> float:
    """Return the weight multiplier for an indicator based on its rolling
    hit rate. Used by the composite scorer.

    Uses 5-day hit rate as the primary metric (matches "signal predictive
    over swing horizon" better than 1-day noise).
    """
    if ledger is None:
        ledger = load_ledger()
    stats = ledger.signals.get(indicator_name)
    if not stats or stats.n < MIN_OBS_FOR_WEIGHT:
        return 1.0  # unknown → default weight
    hit_rate = stats.hit_rate_5d
    if hit_rate < 0.45 and stats.n >= 60:
        return LOWER_WEIGHT   # silenced
    if hit_rate > 0.60:
        return UPPER_WEIGHT   # boosted
    # Linear interpolation in the 0.45..0.60 band
    return 1.0 + (hit_rate - 0.525) * 2  # ~0.85 at hit=0.45, ~1.15 at hit=0.60


def _next_return_pct(dates_sorted: list[str], closes: dict[str, float],
                     as_of: str, lookahead: int) -> float | None:
    """Return the % change from as_of close to as_of+lookahead close."""
    if as_of not in dates_sorted:
        return None
    idx = dates_sorted.index(as_of)
    if idx + lookahead >= len(dates_sorted):
        return None
    p_now = closes[as_of]
    p_future = closes[dates_sorted[idx + lookahead]]
    if not p_now:
        return None
    return (p_future / p_now - 1) * 100


def update_from_journal(
    journal_dir: Path,
    price_provider,   # callable(symbol, as_of, lookback_days) -> {'ohlcv': [...]}
    ledger_path: Path | None = None,
) -> SignalLedger:
    """Walk the journal, join each fired signal with next-1d/5d returns,
    update the ledger. Idempotent — re-running processes only records
    newer than the ledger's last update.

    Args:
        journal_dir: e.g. logs/decisions/
        price_provider: function that returns OHLCV for a symbol.
                        Use argos.data.nepse_ohlcv.load_nepse_ohlcv.
        ledger_path: optional override for ledger file.
    """
    ledger = load_ledger(ledger_path)
    # Only use cutoff if the ledger already has data — otherwise a fresh
    # ledger's generated_at timestamp would skip all past records.
    cutoff = ledger.generated_at if ledger.signals else ""
    logger.info("Updating ledger — cutoff %s", cutoff or "(all history)")

    # Pre-scan MAX journal date per symbol so we fetch prices once through
    # that date + a 10-day lookahead buffer, avoiding the "cached too
    # early" bug where the first fetch was through the OLDEST journal date.
    all_journals: list[tuple[str, str, Path]] = []
    max_date_per_symbol: dict[str, str] = {}
    for jf in sorted(journal_dir.glob("*_*.jsonl")):
        if jf.stem.endswith("combined"):
            continue
        stem = jf.stem
        parts = stem.rsplit("_", 1)
        if len(parts) != 2 or len(parts[1]) != 10:
            continue
        symbol, date = parts[0], parts[1]
        all_journals.append((symbol, date, jf))
        if date > max_date_per_symbol.get(symbol, ""):
            max_date_per_symbol[symbol] = date

    price_cache: dict[str, tuple[list[str], dict[str, float]]] = {}

    def _prices(symbol: str):
        if symbol not in price_cache:
            latest = max_date_per_symbol.get(symbol) or datetime.now().strftime("%Y-%m-%d")
            data = price_provider(symbol, latest, lookback_days=800)
            dates_sorted = [r["date"] for r in data["ohlcv"]]
            closes = {r["date"]: r["close"] for r in data["ohlcv"]}
            price_cache[symbol] = (dates_sorted, closes)
        return price_cache[symbol]

    n_updates = 0

    for symbol, date, jf in all_journals:
        try:
            for line in jf.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                attempted_at = rec.get("attempted_at", "")
                if cutoff and attempted_at <= cutoff:
                    continue

                fired = rec.get("indicator_signals_fired") or []
                if not fired:
                    continue

                try:
                    dates_sorted, closes = _prices(symbol)
                except Exception:
                    continue

                ret_1d = _next_return_pct(dates_sorted, closes, date, 1)
                ret_5d = _next_return_pct(dates_sorted, closes, date, 5)
                if ret_1d is None:
                    continue

                for sig in fired:
                    name = sig.get("indicator")
                    direction = sig.get("direction")
                    if not name or direction not in ("bullish", "bearish"):
                        continue
                    st = ledger.signals.setdefault(name, SignalStats())
                    st.n += 1
                    is_bull = direction == "bullish"
                    if (is_bull and ret_1d > 0) or (not is_bull and ret_1d < 0):
                        st.hits_1d += 1
                    if ret_5d is not None:
                        if (is_bull and ret_5d > 0) or (not is_bull and ret_5d < 0):
                            st.hits_5d += 1
                    st.last_seen = date
                    n_updates += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Skipping %s: %s", jf, e)

    save_ledger(ledger, ledger_path)
    logger.info("Ledger updated: %d signal observations added, %d signals tracked total",
                n_updates, len(ledger.signals))
    return ledger
