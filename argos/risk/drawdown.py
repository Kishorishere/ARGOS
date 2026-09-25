"""Equity drawdown circuit-breaker (P20).

Tracks portfolio equity over time and returns a `size_scale` in (0..1]
that shrinks position sizes as drawdown grows.

Rules (tunable via env):
  DD from peak < 5%      → 1.0 (full risk)
  5% ≤ DD < 8%           → 0.75
  8% ≤ DD < 15%          → 0.50  (half-Kelly haircut)
  15% ≤ DD < 20%         → 0.25  (quarter-Kelly only)
  DD ≥ 20%               → 0.0   (halt all new entries; exits/adjustments only)

Reset: equity recovers to peak × 0.98 → back to full risk.

Storage: equity history is derived from the journal + simulator each run
(read-only view; no separate database needed).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

EQUITY_PATH = Path(__file__).resolve().parents[2] / "data" / "equity_curve.jsonl"


DrawdownStatus = Literal["normal", "yellow", "orange", "red", "halt"]


@dataclass
class DrawdownState:
    equity: float
    peak_equity: float
    drawdown_pct: float
    status: DrawdownStatus
    size_scale: float
    reason: str


def size_scale_from_dd(dd_pct: float) -> tuple[float, DrawdownStatus, str]:
    """Return (size_multiplier, status, reason) for a given drawdown %.

    dd_pct is NEGATIVE (e.g., -8.5 for 8.5% drawdown).
    """
    dd = abs(min(0.0, dd_pct))  # positive number
    if dd < 5:
        return (1.0, "normal", f"DD {dd:.1f}%  full risk")
    if dd < 8:
        return (0.75, "yellow", f"DD {dd:.1f}%  size × 0.75")
    if dd < 15:
        return (0.50, "orange", f"DD {dd:.1f}%  size × 0.50 (half-Kelly haircut)")
    if dd < 20:
        return (0.25, "red", f"DD {dd:.1f}%  size × 0.25 (quarter-Kelly)")
    return (0.0, "halt", f"DD {dd:.1f}%  HALT — no new entries")


def append_equity_point(date: str, equity: float) -> None:
    EQUITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EQUITY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date, "equity": equity}) + "\n")


def drawdown_state(current_equity: float | None = None) -> DrawdownState:
    """Read equity history + current equity → DrawdownState."""
    history: list[dict] = []
    if EQUITY_PATH.exists():
        for line in EQUITY_PATH.read_text().splitlines():
            if line.strip():
                try:
                    history.append(json.loads(line))
                except Exception:
                    continue
    equities = [h["equity"] for h in history]
    if current_equity is not None:
        equities.append(current_equity)
    if not equities:
        return DrawdownState(equity=0.0, peak_equity=0.0, drawdown_pct=0.0,
                             status="normal", size_scale=1.0,
                             reason="no equity history")
    equity = equities[-1]
    peak = max(equities)
    dd = (equity / peak - 1) * 100 if peak > 0 else 0.0
    scale, status, reason = size_scale_from_dd(dd)
    return DrawdownState(equity=equity, peak_equity=peak, drawdown_pct=round(dd, 3),
                         status=status, size_scale=scale, reason=reason)
