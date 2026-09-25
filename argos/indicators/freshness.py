"""Signal freshness / age-decay (P14).

Every time an indicator SIGNAL fires (not just the raw value — the
threshold-crossing event), we record the bar's date in a persistent
JSON. On subsequent evaluations we compute `age_bars = today - last_fired`.
Fresh signals (age 0-2 bars) get a small weight boost; stale signals
(age > 8 bars) decay exponentially.

Rationale:
- An RSI crossed below 30 TODAY is a much fresher setup than one that
  crossed 15 bars ago and is still hanging around. Traders act on
  fresh triggers, not stale conditions.
- Formula: `mult = exp(-age / 5)` — half-life ~3.5 bars, tail decays
  to ~0.15 by bar 10.

Storage: `data/signal_freshness.json` with structure:
    {
      "NABIL": {"RSI(14) oversold": "2024-12-15", ...},
      "ADBL": {...},
      ...
    }

Called from `engine.compute_snapshot()` — right before the per-signal
weights are summed into composite_bull / composite_bear.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FRESHNESS_PATH = Path(os.environ.get("ARGOS_FRESHNESS_STATE",
                                      str(Path(__file__).resolve().parents[2] / "data" / "signal_freshness.json")))

HALF_LIFE_BARS = 5  # multiplier decays to 0.5 at 5 bars
MAX_BOOST = 1.20    # freshest signals boosted 20%
MIN_MULT = 0.15     # stale signals never fully silenced


_STATE: dict | None = None


def _load_state() -> dict:
    global _STATE
    if _STATE is None:
        if FRESHNESS_PATH.exists():
            try:
                _STATE = json.loads(FRESHNESS_PATH.read_text())
            except Exception:
                _STATE = {}
        else:
            _STATE = {}
    return _STATE


def _save_state() -> None:
    if _STATE is None:
        return
    FRESHNESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FRESHNESS_PATH.write_text(json.dumps(_STATE, indent=2))


def _signal_key(indicator: str, direction: str, threshold: str | None) -> str:
    """Deduplicating key: an "RSI(14) bullish <30" firing is a
    different event from "RSI(14) bearish >70".
    """
    return f"{indicator} {direction} {threshold or ''}"


def age_bars(symbol: str, indicator: str, direction: str,
             threshold: str | None, as_of_date: str) -> int | None:
    """How many bars since this signal last fired for this symbol.
    Returns None if never seen before.
    """
    state = _load_state()
    key = _signal_key(indicator, direction, threshold)
    last = (state.get(symbol.upper()) or {}).get(key)
    if not last:
        return None
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d")
        cur_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
        return max(0, (cur_dt - last_dt).days)
    except ValueError:
        return None


def record_firing(symbol: str, indicator: str, direction: str,
                  threshold: str | None, as_of_date: str) -> None:
    """Note that this signal fired on this bar."""
    state = _load_state()
    sym = symbol.upper()
    state.setdefault(sym, {})[_signal_key(indicator, direction, threshold)] = as_of_date


def freshness_multiplier(age: int | None) -> float:
    """Boost fresh (age 0-1), decay stale (age > 3).

    age None  → 1.20  (never seen before = maximally fresh)
    age 0     → 1.20  (fired today = boost)
    age 1     → 1.10
    age 3     → 0.75
    age 5     → 0.50  (half-life)
    age 8     → 0.30
    age >=15  → floor
    """
    if age is None or age < 0 or age == 0:
        return MAX_BOOST
    if age <= 1:
        return 1.10
    return max(MIN_MULT, math.exp(-age / HALF_LIFE_BARS))


def apply_freshness(symbol: str, as_of_date: str, fired_signals: list) -> list:
    """For each fired signal: (a) record its firing, (b) look up prior
    firing to compute age, (c) apply multiplier to weight. Returns a
    NEW list of ThresholdSignal copies with adjusted weights.
    """
    out = []
    for sig in fired_signals:
        age = age_bars(symbol, sig.indicator, sig.direction, sig.threshold, as_of_date)
        mult = freshness_multiplier(age)
        # Record the firing regardless — even a stale re-fire refreshes state
        record_firing(symbol, sig.indicator, sig.direction, sig.threshold, as_of_date)
        adjusted = sig.model_copy(update={
            "weight": sig.weight * mult,
            "note": f"{sig.note} [fresh×{mult:.2f}, age={age}]" if age else sig.note,
        })
        out.append(adjusted)
    _save_state()
    return out
