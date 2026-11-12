"""Trigger primitive — one shape for every reason the system should wake up.

A Trigger is a single reason the research desk should pay attention to a
ticker today, emitted independently of whether we end up running the
committee. Triggers are:

- *computed*, never guessed — every `check()` in this package reads from
  cached OHLCV or yfinance, never from an LLM;
- *typed* — kind + direction + magnitude so the paper's selective-reasoning
  analysis can bucket fires the same way the edge gate does;
- *idempotent* on `(ticker, as_of)` — re-running the same date produces
  the same triggers, which keeps the event-loop script re-startable;
- *reproducible* — every Trigger carries a one-line `detail` string so a
  human (or the gate prompt, when we eventually wire that A/B) can see
  exactly which rule fired and with what number.

The gate prompt is not yet rewired to read triggers — that change needs a
paired ablation (`compare_gate_with_triggers.py`) before it ships, per
CLAUDE.md's prompt-change rule. For Phase B, triggers surface via the API
and the event-loop script only; they travel alongside the pipeline when
the loop calls it, but no node reads them yet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

TriggerSource = Literal["technical", "scheduled", "fundamental", "news"]
TriggerDirection = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class Trigger:
    ticker: str
    as_of: str                      # YYYY-MM-DD
    kind: str                       # e.g. "rsi_cross_70", "earnings_within_3d"
    direction: TriggerDirection
    magnitude: float                # kind-specific numeric (RSI value, days-to-event, z-score, …)
    detail: str                     # one-line human-readable (cite the number)
    source: TriggerSource

    def to_dict(self) -> dict:
        return asdict(self)
