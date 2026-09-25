"""Decision journal — the audit trail every Argos run writes to disk.

Design goals:
- **Append-only**: multiple runs on the same date become multiple records
  with distinct `attempt_id`s; nothing is silently overwritten.
- **Reproducible**: every record carries a git SHA (`argos_version`), a
  UUID `attempt_id`, and an ISO timestamp.
- **Traceable**: every fired indicator is listed with its value, threshold,
  direction, weight and contribution — a human reviewing a bad trade can see
  exactly which technical signal(s) pushed the decision that way.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _git_sha() -> str:
    """Best-effort git SHA of the working tree. Falls back to 'unknown'."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _flatten_indicator_signals(technicals_signal: dict | None) -> list[dict]:
    """Extract every fired indicator signal with its contribution to the composite score."""
    if not technicals_signal:
        return []
    snap = technicals_signal.get("indicator_snapshot")
    if not snap:
        return []
    fired = snap.get("fired_signals") or []
    out: list[dict] = []
    for sig in fired:
        weight = float(sig.get("weight") or 0.0)
        direction = sig.get("direction") or "neutral"
        contribution = weight if direction == "bullish" else (-weight if direction == "bearish" else 0.0)
        out.append({
            "indicator": sig.get("indicator"),
            "value": sig.get("value"),
            "threshold": sig.get("threshold"),
            "direction": direction,
            "weight": weight,
            "contribution": contribution,
            "note": sig.get("note", ""),
        })
    return out


def _indicator_composite(technicals_signal: dict | None) -> dict:
    if not technicals_signal:
        return {}
    snap = technicals_signal.get("indicator_snapshot")
    if not snap:
        return {}
    return {
        "composite_bull_score": snap.get("composite_bull_score"),
        "composite_bear_score": snap.get("composite_bear_score"),
        "net_bias_score": snap.get("net_bias_score"),
        "trend_regime": (snap.get("trend") or {}).get("regime"),
        "vol_regime": (snap.get("volatility") or {}).get("vol_regime"),
        "bull_ringer_score": (snap.get("candles") or {}).get("bull_ringer_score"),
        "bear_ringer_score": (snap.get("candles") or {}).get("bear_ringer_score"),
    }


def build_record(
    ticker: str,
    date: str,
    final_state: dict,
    ohlcv_summary: dict,
    duration_s: float,
    argos_version: str | None = None,
) -> dict:
    """Assemble one journal record from a completed graph run."""
    technicals_signal = final_state.get("technicals_signal") or {}
    return {
        # ── Metadata ──
        "attempt_id": str(uuid.uuid4()),
        "attempted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "argos_version": argos_version or _git_sha(),
        "duration_s": round(duration_s, 3),
        "ticker": ticker,
        "date": date,
        "run_id": final_state.get("run_id"),
        # ── Input data summary ──
        "ohlcv_summary": ohlcv_summary,
        # ── Portfolio context passed to the risk manager ──
        "portfolio_state": final_state.get("portfolio_state", {}),
        # ── Analyst signals ──
        "analyst_signals": {
            "fundamentals": final_state.get("fundamentals_signal"),
            "sentiment": final_state.get("sentiment_signal"),
            "technicals": technicals_signal,
        },
        # ── Kronos forecast (raw) ──
        "kronos_forecast": final_state.get("kronos_forecast"),
        # ── Multi-horizon forecast (P17) — 1d/1w/1m direction+profit ──
        "multi_horizon_forecast": final_state.get("multi_horizon_forecast"),
        # ── Indicator trace: every fired signal + composite scores ──
        "indicator_composite": _indicator_composite(technicals_signal),
        "indicator_signals_fired": _flatten_indicator_signals(technicals_signal),
        # ── Debate (structured) ──
        "debate": {
            "bull": final_state.get("bull_argument"),
            "bear": final_state.get("bear_argument"),
            "rounds_completed": max(final_state.get("debate_round", 1) - 1, 1),
        },
        "debate_transcript": final_state.get("debate_transcript", []),
        # ── Risk verdict ──
        "risk_verdict": final_state.get("risk_verdict"),
        # ── Final decision ──
        "final_decision": final_state.get("final_decision"),
        # ── Full audit: exact prompts sent to each agent ──
        "analyst_prompts": final_state.get("analyst_prompts", {}),
        # ── Errors collected (non-fatal) ──
        "errors": final_state.get("errors", []),
    }


def append_record(record: dict, output_dir: Path, ticker: str, date: str) -> Path:
    """Append the record to logs/decisions/{ticker}_{date}.jsonl.

    Files are append-only — repeated runs on the same date become multiple
    JSONL records with distinct attempt_ids, ordered by attempted_at.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{ticker}_{date}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return path


def date_already_journalled(output_dir: Path, ticker: str, date: str) -> bool:
    return (output_dir / f"{ticker}_{date}.jsonl").exists()
