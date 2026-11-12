"""argos.triggers — reasons the research desk should wake up for a ticker.

Public API:
  from argos.triggers import check_all, Trigger

  for t in check_all("NVDA", "2026-11-09"):
      print(t.kind, t.direction, t.detail)

Rule modules (technical, scheduled) are intentionally cheap and read-only.
Fundamental and news triggers are stubbed and land in Phase C with their
data sources. The event-loop script (scripts/event_loop.py) is the live
consumer; the API route (GET /api/triggers/{ticker}) is the frontend's.

The gate prompt deliberately does NOT yet read triggers — that change
needs a paired ablation to be publishable, per CLAUDE.md. Triggers in
Phase B are observable, not load-bearing on the LLM pipeline.
"""

from __future__ import annotations

import logging

from argos.triggers.base import Trigger, TriggerDirection, TriggerSource

logger = logging.getLogger(__name__)


def check_all(ticker: str, as_of: str) -> list[Trigger]:
    """Run every trigger module for one (ticker, as_of). Modules that
    error out are logged and skipped — triggers are advisory, never
    allowed to crash a caller like the event loop."""
    out: list[Trigger] = []

    # lazy — yfinance / requests import cost deferred to first use
    from argos.triggers import fundamental, news, scheduled, technical
    for mod in (technical, scheduled, fundamental, news):
        try:
            out.extend(mod.check(ticker, as_of))
        except Exception as e:  # noqa: BLE001
            logger.warning("trigger module %s failed on %s@%s: %s", mod.__name__, ticker, as_of, e)
    return out


__all__ = ["Trigger", "TriggerDirection", "TriggerSource", "check_all"]
