"""Committee-confidence reliability curve — "when the system says 0.7, is it 0.7?"

Walks the reference run (`logs/decisions/nasdaq_focus/`), pairs every
journalled `final_decision.confidence` with the realized directional
move at horizon h, and bins by confidence to produce the reliability
curve a trader (or a reviewer) wants to see next to a live verdict.

This is NOT the ensemble isotonic in `argos/learn/calibration.py` —
that one calibrates `probability_up` outputs from the Kronos/LightGBM
stack. This file calibrates the Portfolio Manager's committee confidence,
which is the number the UI displays to the user.

Cached per (horizon,) so repeated API calls don't re-walk the journals.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger(__name__)

NASDAQ_ROOT = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"

# 10 fixed buckets [0, .1), [.1, .2), …, [.9, 1.0]
_BIN_EDGES: list[float] = [i / 10 for i in range(11)]

# Simple in-process cache so repeated calls don't re-walk the journals.
_CACHE: dict[int, dict] = {}


def _reset_cache() -> None:
    _CACHE.clear()


def _load_ticker_records(root: Path, ticker: str) -> list[dict]:
    """Thin wrapper around scripts.eval_nasdaq._load_ticker_records so we
    don't double-maintain the journal format knowledge."""
    from scripts.eval_nasdaq import _load_ticker_records as impl
    return impl(root, ticker)


def _collect_scored(horizon: int) -> list[tuple[float, bool]]:
    """Return a list of (confidence, was_correct) across every ticker in
    the reference run. 'was_correct' means the final_decision action and
    the realized return at `horizon` days have the same sign.
    """
    from scripts.eval_nasdaq import (
        _direction_from_action, _realized_returns, HORIZONS,
    )
    if horizon not in HORIZONS:
        # Fall back to the nearest supported horizon so the UI always gets
        # a reliable answer. HORIZONS is small and sorted.
        horizon = min(HORIZONS, key=lambda h: abs(h - horizon))

    tickers = sorted({p.name for p in NASDAQ_ROOT.iterdir()
                      if p.is_dir()}) if NASDAQ_ROOT.exists() else []

    pairs: list[tuple[float, bool]] = []
    for t in tickers:
        records = _load_ticker_records(NASDAQ_ROOT, t)
        good = [r for r in records if not r.get("error") and r.get("date")]
        if not good:
            continue
        dates = [r["date"] for r in good]
        realized = _realized_returns(t, dates)
        for r in good:
            fd = r.get("final_decision") or {}
            conf = fd.get("confidence")
            action = fd.get("action")
            if not isinstance(conf, (int, float)):
                continue
            d_idx = HORIZONS.index(horizon)
            ret = realized.get(r["date"], [None] * len(HORIZONS))[d_idx]
            if ret is None:
                continue
            dir_pred = _direction_from_action(action)
            if dir_pred is None:
                # HOLD / abstention — we score coverage separately; it
                # has no "right answer" for directional calibration.
                continue
            dir_real = 1 if ret > 0 else -1 if ret < 0 else 0
            correct = (dir_pred == dir_real)
            pairs.append((float(conf), correct))
    return pairs


def reliability_curve(horizon: int = 5) -> dict:
    """Produce the per-bin empirical accuracy at `horizon`. Cached."""
    cached = _CACHE.get(horizon)
    if cached is not None:
        return cached

    pairs = _collect_scored(horizon)
    n_scored = len(pairs)

    bins: list[dict] = []
    for i in range(len(_BIN_EDGES) - 1):
        lo, hi = _BIN_EDGES[i], _BIN_EDGES[i + 1]
        bucket = [c for c in pairs if lo <= c[0] < hi or (i == len(_BIN_EDGES) - 2 and c[0] == 1.0)]
        n = len(bucket)
        acc: Optional[float] = None
        if n:
            acc = sum(1 for _, ok in bucket if ok) / n
        bins.append({
            "confidence_bin_lo": lo,
            "confidence_bin_hi": hi,
            "n": n,
            "empirical_accuracy": acc,
        })

    overall: Optional[float] = None
    if n_scored:
        overall = sum(1 for _, ok in pairs if ok) / n_scored

    out = {
        "horizon": horizon,
        "n_records": sum(b["n"] for b in bins),
        "n_scored": n_scored,
        "overall_accuracy": overall,
        "bins": bins,
    }
    _CACHE[horizon] = out
    return out


def calibrate(raw_confidence: float, horizon: int = 5) -> Optional[float]:
    """Map a raw committee confidence to the empirical accuracy of the
    bin it falls into. Returns None if that bin has too few samples
    (< 3) to make a claim."""
    if not isinstance(raw_confidence, (int, float)):
        return None
    curve = reliability_curve(horizon)
    for b in curve["bins"]:
        if b["confidence_bin_lo"] <= raw_confidence < b["confidence_bin_hi"] or (
            b["confidence_bin_hi"] >= 1.0 and raw_confidence == 1.0
        ):
            if b["n"] < 3 or b["empirical_accuracy"] is None:
                return None
            return float(b["empirical_accuracy"])
    return None
