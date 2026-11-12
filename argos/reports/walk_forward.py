"""Walk-forward calibration scoring.

The reference backtest run calibrates its confidence curve against the
WHOLE history at once — which is a form of in-sample peeking (the
calibration bin for date D uses dates after D). For the paper to claim
a *calibrated* system, the curve a trader would see on date D must be
built only from dates strictly before D.

This module provides exactly that:

  score_walk_forward(journal_root, horizon, min_prior) → list[dict]

For each (ticker, date) in the reference run, in chronological order:
  1. Fit the reliability curve on journals strictly earlier than the date
     (per ticker; we don't pool across tickers because confidence
     distributions differ noticeably — TSLA refusals vs NVDA bets).
  2. Read the date's committee confidence; map to a calibrated prob via
     the prior-only curve.
  3. Score directional correctness at horizon h.

The output is one JSONL row per decision + a summary table with:
  - n_scored (per ticker, aggregate)
  - raw-vs-calibrated Brier score
  - empirical hit rate at the top confidence quartile (vs bottom)
  - selective-prediction curve: coverage % → accuracy if we'd traded
    only the top-K most-confident decisions

This is a walk-forward *evaluation*, not a runtime. Nothing is re-run;
no new LLM calls. It reads the reference run and re-scores it honestly.
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.eval_nasdaq import (  # noqa: E402
    HORIZONS,
    _direction_from_action,
    _load_ticker_records,
    _realized_returns,
)

logger = logging.getLogger("argos.walk_forward")

# Reliability-curve binning (10 buckets, same as serve-time calibration).
_BIN_EDGES: list[float] = [i / 10 for i in range(11)]


@dataclass
class ScoredPoint:
    ticker: str
    date: str
    action: str
    raw_confidence: float
    walk_fwd_calibrated: Optional[float]
    realized_dir: Optional[int]
    predicted_dir: int
    correct: Optional[bool]


# ── Curve math ──────────────────────────────────────────────────────────────

def _bin_of(conf: float) -> tuple[float, float]:
    for i in range(len(_BIN_EDGES) - 1):
        lo, hi = _BIN_EDGES[i], _BIN_EDGES[i + 1]
        if lo <= conf < hi or (i == len(_BIN_EDGES) - 2 and conf == 1.0):
            return lo, hi
    return 0.0, 0.1


def _accuracy_in_bin(points: list[ScoredPoint], lo: float, hi: float, min_prior: int) -> Optional[float]:
    """Walk-forward curve must have at least `min_prior` samples in a
    bin for that bin to produce a calibrated output; otherwise None."""
    hits = [p for p in points if lo <= p.raw_confidence < hi or (hi >= 1.0 and p.raw_confidence == 1.0)]
    if len(hits) < min_prior:
        return None
    correct = sum(1 for p in hits if p.correct is True)
    return correct / len(hits)


# ── Scoring ─────────────────────────────────────────────────────────────────

def score_walk_forward(
    journal_root: Path,
    horizon: int = 5,
    min_prior: int = 3,
) -> dict:
    """Walk the reference run, compute per-ticker walk-forward calibration,
    and emit a research summary."""
    if horizon not in HORIZONS:
        horizon = min(HORIZONS, key=lambda h: abs(h - horizon))
    h_idx = HORIZONS.index(horizon)

    per_ticker: dict[str, list[ScoredPoint]] = {}
    tickers = sorted({p.name for p in journal_root.iterdir()
                      if p.is_dir()}) if journal_root.exists() else []

    for t in tickers:
        records = _load_ticker_records(journal_root, t)
        good = [r for r in records if not r.get("error") and r.get("date")]
        good.sort(key=lambda r: r["date"])
        if not good:
            continue
        realized = _realized_returns(t, [r["date"] for r in good])
        prior_points: list[ScoredPoint] = []
        scored: list[ScoredPoint] = []
        for r in good:
            fd = r.get("final_decision") or {}
            conf = fd.get("confidence")
            if not isinstance(conf, (int, float)):
                continue
            action = str(fd.get("action") or "").lower()
            dir_pred = _direction_from_action(action)
            ret = realized.get(r["date"], [None] * len(HORIZONS))[h_idx]
            if dir_pred is None or ret is None:
                # Abstention or missing realized return — don't score,
                # don't contribute to the prior.
                continue
            dir_real = 1 if ret > 0 else -1 if ret < 0 else 0
            correct = (dir_pred == dir_real)

            # Walk-forward calibration: use only PRIOR points for this ticker.
            lo, hi = _bin_of(float(conf))
            calibrated = _accuracy_in_bin(prior_points, lo, hi, min_prior)

            point = ScoredPoint(
                ticker=t, date=r["date"], action=action,
                raw_confidence=float(conf),
                walk_fwd_calibrated=calibrated,
                realized_dir=dir_real,
                predicted_dir=dir_pred,
                correct=correct,
            )
            scored.append(point)
            prior_points.append(point)
        per_ticker[t] = scored

    # ── Aggregate stats ────────────────────────────────────────────────
    total = sum(len(v) for v in per_ticker.values())

    def brier(points: list[ScoredPoint], use_calibrated: bool) -> Optional[float]:
        if not points:
            return None
        acc = 0.0
        n = 0
        for p in points:
            if use_calibrated and p.walk_fwd_calibrated is None:
                continue
            prob = p.walk_fwd_calibrated if use_calibrated else p.raw_confidence
            # We bet on the predicted direction; outcome is 1 if right.
            outcome = 1 if p.correct else 0
            acc += (prob - outcome) ** 2
            n += 1
        return acc / n if n else None

    per_ticker_summary: list[dict] = []
    for t, points in per_ticker.items():
        n = len(points)
        accurate = sum(1 for p in points if p.correct)
        acc = accurate / n if n else None
        # Selective-prediction: accuracy on the top quartile by raw conf.
        if n >= 4:
            sorted_pts = sorted(points, key=lambda p: p.raw_confidence, reverse=True)
            top = sorted_pts[: max(1, n // 4)]
            top_acc = sum(1 for p in top if p.correct) / len(top)
        else:
            top_acc = None
        per_ticker_summary.append({
            "ticker": t,
            "n": n,
            "accuracy": acc,
            "top_quartile_accuracy": top_acc,
            "brier_raw": brier(points, use_calibrated=False),
            "brier_walkforward": brier(points, use_calibrated=True),
            "n_with_calibration": sum(1 for p in points if p.walk_fwd_calibrated is not None),
        })

    agg_points = [p for pts in per_ticker.values() for p in pts]
    aggregate = {
        "n_tickers": len(per_ticker),
        "n_scored": total,
        "accuracy": (sum(1 for p in agg_points if p.correct) / total) if total else None,
        "brier_raw": brier(agg_points, use_calibrated=False),
        "brier_walkforward": brier(agg_points, use_calibrated=True),
    }

    return {
        "horizon": horizon,
        "min_prior": min_prior,
        "per_ticker": per_ticker_summary,
        "aggregate": aggregate,
        "points": [
            {
                "ticker": p.ticker, "date": p.date, "action": p.action,
                "raw_conf": p.raw_confidence,
                "walk_fwd_cal": p.walk_fwd_calibrated,
                "correct": p.correct,
            }
            for p in agg_points
        ],
    }


def render_markdown(result: dict) -> str:
    L = ["# Walk-forward calibration — committee confidence", ""]
    L.append(f"**Research question:** _When the system said 0.7 on date D, what was its "
             f"empirical hit rate on bins of prior-only history?_ "
             f"(horizon h={result['horizon']}, min_prior={result['min_prior']})")
    L.append("")

    L.append("## Aggregate")
    a = result["aggregate"]
    L.append(f"- Tickers scored: {a['n_tickers']}")
    L.append(f"- Decisions scored: {a['n_scored']}")
    L.append(f"- Overall accuracy: {_pct(a.get('accuracy'))}")
    L.append(f"- Brier (raw confidence): {_num(a.get('brier_raw'), 3)}")
    L.append(f"- Brier (walk-forward calibrated): {_num(a.get('brier_walkforward'), 3)}")
    L.append("")
    L.append("Lower Brier = better. If walk-forward Brier is materially higher than raw, "
             "the system's confidence numbers are out-of-sample noise, not signal.")
    L.append("")

    L.append("## Per ticker")
    L.append("| Ticker | n | Accuracy | Top-quartile acc | Brier raw | Brier wf | n with cal |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in result["per_ticker"]:
        L.append("| " + " | ".join([
            row["ticker"], str(row["n"]),
            _pct(row.get("accuracy")),
            _pct(row.get("top_quartile_accuracy")),
            _num(row.get("brier_raw"), 3),
            _num(row.get("brier_walkforward"), 3),
            str(row.get("n_with_calibration") or 0),
        ]) + " |")
    L.append("")

    L.append("The *top-quartile accuracy* column answers the selective-prediction question: "
             "if we had only traded the system's most-confident 25% of calls, what would hit rate "
             "have been? Higher-than-average means confidence predicts correctness — "
             "the headline calibration claim for the paper.")

    return "\n".join(L)


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v: Optional[float], sig: int = 2) -> str:
    return "—" if v is None else f"{v:.{sig}f}"
