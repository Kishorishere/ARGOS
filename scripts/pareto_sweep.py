"""Cost--recall Pareto frontier for the pre-debate gate.

Produces Figure 2 of paper.tex: per-ticker curves of
  x = token savings (% vs full-committee baseline)
  y = true-trade recall (fraction of baseline's directional calls preserved)
at a swept gate-aggressiveness threshold.

Research question answered:
  "If we had been more or less aggressive with the gate, how would cost and
   recall have traded off?"

Why this script matters: running the LLM gate at many thresholds would cost
many thousands of tokens. Instead we re-score every baseline decision with a
*heuristic gate* that reads the SAME trigger vocabulary the LLM gate reads
(from the journal's analyst signals + indicator snapshot) and sweep the
threshold post-hoc on existing data. Zero new LLM calls.

Thresholded policy:
  proceed(x_t) = 1[score(x_t) >= tau]
  where score(x_t) is the max aggressiveness of any trigger firing at x_t.

On "skip" the simulator would have emitted HOLD at gate cost only; on
"proceed" the baseline's actual final_decision + tokens apply. We compare
the thresholded policy to the untouched baseline policy per ticker.

Inputs:
  baseline journals: logs/decisions/<universe>/<TICKER>/<TICKER>_<DATE>.jsonl
Outputs:
  reports/gate_ab/pareto.json         machine-readable
  reports/gate_ab/pareto.md           headline-table markdown
  reports/gate_ab/pareto.png          per-ticker curves

Usage:
  python scripts/pareto_sweep.py \
      --baseline logs/decisions/nasdaq_focus \
      --universe data/universe/nasdaq_focus.json \
      --out reports/gate_ab
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env", override=True)

from scripts.eval_nasdaq import (  # noqa: E402
    HORIZONS,
    _direction_from_action,
    _load_ticker_records,
    _realized_returns,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pareto_sweep")

# Gate's own token cost. Flash-lite + short structured output. Measured
# empirically from argos/nodes/edge_gate.py runs in logs.
GATE_TOKENS_PER_CALL = 1500


# ── Heuristic trigger scoring ────────────────────────────────────────────


def _analyst_sig(rec: dict, name: str) -> dict:
    """Return the analyst signal subdict (handles missing)."""
    sigs = rec.get("analyst_signals") or {}
    s = sigs.get(name)
    return s if isinstance(s, dict) else {}


def _snap(rec: dict) -> dict:
    """Return the indicator snapshot (may be missing / empty)."""
    tech = (rec.get("analyst_signals") or {}).get("technicals") or {}
    return tech.get("indicator_snapshot") or {}


def _conf(sig: dict) -> float:
    try:
        return float(sig.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _direction_from_signal_str(signal_str) -> int:
    """bullish -> +1, bearish -> -1, else 0."""
    if not signal_str:
        return 0
    s = str(signal_str).lower()
    if s == "bullish":
        return +1
    if s == "bearish":
        return -1
    return 0


def _score_triggers(rec: dict) -> tuple[float, list[str]]:
    """Compute a heuristic aggressiveness score in [0, 1] for one decision.

    Score is max of the per-trigger strengths. We also return which triggers
    fired at a modest threshold, mirroring the LLM gate's `triggers` list.
    """
    fund = _analyst_sig(rec, "fundamentals")
    sent = _analyst_sig(rec, "sentiment")
    tech = _analyst_sig(rec, "technicals")
    snap = _snap(rec)

    strengths: dict[str, float] = {}

    # 1) Analyst conviction — any single analyst with high confidence
    strengths["analyst_conviction"] = max(_conf(fund), _conf(sent), _conf(tech)) or 0.0

    # 2) Analyst disagreement — two analysts on opposite sides, both with
    #    non-trivial confidence
    dirs = [
        (_direction_from_signal_str(fund.get("signal")), _conf(fund)),
        (_direction_from_signal_str(sent.get("signal")), _conf(sent)),
        (_direction_from_signal_str(tech.get("signal")), _conf(tech)),
    ]
    dis = 0.0
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            di, ci = dirs[i]
            dj, cj = dirs[j]
            if di != 0 and dj != 0 and di != dj:
                dis = max(dis, min(ci, cj))
    strengths["analyst_disagreement"] = dis

    # 3) Composite bias skew — |net_bias_score|, normalized by typical range
    net_bias = snap.get("net_bias_score")
    try:
        net_bias_abs = abs(float(net_bias)) if net_bias is not None else 0.0
    except (TypeError, ValueError):
        net_bias_abs = 0.0
    # Empirical normalization: net_bias rarely exceeds ~2.0 in Argos snapshots
    strengths["composite_bias_skew"] = min(net_bias_abs / 2.0, 1.0)

    # 4) Vol spike / high vol regime
    vol_regime = ((snap.get("volatility") or {}).get("vol_regime") or "").lower()
    strengths["vol_regime_high"] = 1.0 if vol_regime == "high" else (
        0.5 if vol_regime == "mid" else 0.0)

    # 5) Pattern fire — ringer scores on candle patterns
    candles = snap.get("candles") or {}
    try:
        bull_r = float(candles.get("bull_ringer_score") or 0.0)
        bear_r = float(candles.get("bear_ringer_score") or 0.0)
    except (TypeError, ValueError):
        bull_r = bear_r = 0.0
    strengths["pattern_fire"] = min(max(bull_r, bear_r) / 3.0, 1.0)

    # Score: max over triggers. Triggers at strength >= 0.5 count as "fired".
    score = max(strengths.values()) if strengths else 0.0
    fired = sorted([t for t, s in strengths.items() if s >= 0.5])
    return score, fired


# ── Sweep ─────────────────────────────────────────────────────────────────


@dataclass
class PointStats:
    """One (ticker, tau) point on the Pareto frontier."""
    ticker: str
    tau: float
    n: int = 0
    n_proceed: int = 0            # heuristic-gate proceeded
    n_baseline_directional: int = 0  # baseline's BUY+SELL count
    n_preserved_directional: int = 0  # baseline's BUY+SELL that still proceed under tau
    n_committee_tokens: int = 0   # baseline committee tokens on dates that would proceed
    n_total_baseline_tokens: int = 0  # all baseline tokens
    dir_acc_h5_proceed: Optional[float] = None  # accuracy on proceeded-direction decisions

    @property
    def skip_rate(self) -> float:
        return 1.0 - (self.n_proceed / self.n) if self.n else 0.0

    @property
    def proceed_rate(self) -> float:
        return self.n_proceed / self.n if self.n else 0.0

    @property
    def true_trade_recall(self) -> Optional[float]:
        if self.n_baseline_directional == 0:
            return None
        return self.n_preserved_directional / self.n_baseline_directional

    @property
    def token_savings_pct(self) -> float:
        """Fractional savings vs baseline (0..1).

        Gated cost = committee tokens on proceeded dates + gate_tokens per dec.
        """
        if self.n_total_baseline_tokens == 0:
            return 0.0
        gated_cost = self.n_committee_tokens + GATE_TOKENS_PER_CALL * self.n
        return max(0.0, 1.0 - gated_cost / self.n_total_baseline_tokens)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "tau": round(self.tau, 3),
            "n": self.n,
            "proceed_rate": round(self.proceed_rate, 4),
            "skip_rate": round(self.skip_rate, 4),
            "true_trade_recall": None if self.true_trade_recall is None
                else round(self.true_trade_recall, 4),
            "token_savings_pct": round(self.token_savings_pct, 4),
            "dir_acc_h5_proceed": None if self.dir_acc_h5_proceed is None
                else round(self.dir_acc_h5_proceed, 4),
            "n_baseline_directional": self.n_baseline_directional,
            "n_preserved_directional": self.n_preserved_directional,
        }


def _sweep_ticker(records: list[dict], ticker: str,
                  taus: list[float]) -> list[PointStats]:
    """Run the Pareto sweep for one ticker over a list of thresholds."""
    good = [r for r in records if not r.get("error") and r.get("date")]
    if not good:
        return []

    # Pre-compute per-decision metadata
    dates = [r["date"] for r in good]
    scores = [_score_triggers(r) for r in good]
    actions = [((r.get("final_decision") or {}).get("action") or "").lower()
               for r in good]
    tokens = [int(((r.get("token_usage") or {}).get("totals") or {}).get(
        "total_tokens", 0)) for r in good]
    realized = _realized_returns(ticker, dates)

    def _is_directional(a: str) -> bool:
        return a in ("buy", "sell", "long", "short")

    out: list[PointStats] = []
    for tau in taus:
        p = PointStats(ticker=ticker, tau=tau, n=len(good))
        p.n_total_baseline_tokens = sum(tokens)
        dir_correct = 0
        dir_total = 0
        for rec, (score, _trig), action, tok, d in zip(
                good, scores, actions, tokens, dates):
            is_dir = _is_directional(action)
            if is_dir:
                p.n_baseline_directional += 1
            if score >= tau:
                p.n_proceed += 1
                p.n_committee_tokens += tok
                if is_dir:
                    p.n_preserved_directional += 1
                    # Directional accuracy at h=5 on proceeded directional dates
                    rets = realized.get(d) or []
                    h5 = rets[1] if len(rets) > 1 else None
                    if h5 is not None and h5 != 0:
                        dir_total += 1
                        direction = _direction_from_action(action)
                        if direction is not None and (
                                (direction > 0 and h5 > 0) or
                                (direction < 0 and h5 < 0)):
                            dir_correct += 1
        if dir_total > 0:
            p.dir_acc_h5_proceed = dir_correct / dir_total
        out.append(p)
    return out


# ── Rendering ─────────────────────────────────────────────────────────────


def _plot_pareto(all_points: dict[str, list[PointStats]], out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; skipping plot")
        return
    fig, ax = plt.subplots(figsize=(8, 6), dpi=120)
    for ticker, points in all_points.items():
        xs = [p.token_savings_pct * 100 for p in points]
        ys = [p.true_trade_recall * 100 if p.true_trade_recall is not None
              else None for p in points]
        # Drop points with undefined recall
        xy = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if not xy:
            continue
        xs2, ys2 = zip(*xy)
        ax.plot(xs2, ys2, marker="o", label=ticker, linewidth=1.5)
    ax.set_xlabel("Token savings vs full-committee baseline (%)")
    ax.set_ylabel("True-trade recall (%)")
    ax.set_title("Cost--recall Pareto frontier\n(heuristic gate sweep, by ticker)")
    ax.axhline(90, color="gray", linestyle="--", linewidth=0.6,
               label="recall=90% target")
    ax.set_xlim(-2, 100)
    ax.set_ylim(0, 105)
    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    logger.info("Pareto plot -> %s", out_path)


def _render_markdown(all_points: dict[str, list[PointStats]]) -> str:
    """Produce the headline table for paper.tex's Figure 2 caption.

    We pick a few canonical thresholds (0.50, 0.65, 0.80) and show the
    tradeoff per ticker at each.
    """
    L = ["# Cost--recall Pareto frontier (heuristic gate sweep)", ""]
    L.append("Produced by `scripts/pareto_sweep.py`. The heuristic gate scores "
             "each decision from the journal's analyst signals + indicator "
             "snapshot using the LLM gate's trigger vocabulary, then thresholds "
             "at `tau` to simulate a more/less aggressive gate without "
             "spending new LLM tokens.")
    L.append("")
    L.append("## Headline table")
    L.append("")
    L.append("| Ticker | tau | proceed% | recall | tok. saved% | h=5 acc on proceed |")
    L.append("|---|---:|---:|---:|---:|---:|")

    def _fmt_pct(v):
        return "—" if v is None else f"{v*100:.1f}%"

    for tau_target in (0.50, 0.65, 0.80):
        for ticker, points in all_points.items():
            # Find closest tau
            p = min(points, key=lambda q: abs(q.tau - tau_target))
            L.append("| " + " | ".join([
                ticker, f"{p.tau:.2f}",
                f"{p.proceed_rate*100:.0f}%",
                _fmt_pct(p.true_trade_recall),
                f"{p.token_savings_pct*100:.1f}%",
                _fmt_pct(p.dir_acc_h5_proceed),
            ]) + " |")
    L.append("")
    L.append("Up-and-to-the-right is better: high recall with high token savings. "
             "See `pareto.png` for the full curves.")
    L.append("")
    return "\n".join(L)


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gate Pareto sweep")
    p.add_argument("--baseline",
                   default=str(REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"))
    p.add_argument("--universe",
                   default=str(REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"))
    p.add_argument("--tickers", default=None,
                   help="Comma-separated override (default: universe file)")
    p.add_argument("--out", default=str(REPO_ROOT / "reports" / "gate_ab"))
    p.add_argument("--tau-steps", type=int, default=21,
                   help="Number of tau values in [0,1] (default 21 = step 0.05)")
    return p.parse_args()


def main():
    args = _parse_args()
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = json.loads(Path(args.universe).read_text())["tickers"]

    baseline_root = Path(args.baseline)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    taus = [i / (args.tau_steps - 1) for i in range(args.tau_steps)]
    logger.info("Sweeping %d tau values on %d tickers from %s",
                len(taus), len(tickers), baseline_root)

    all_points: dict[str, list[PointStats]] = {}
    for t in tickers:
        records = _load_ticker_records(baseline_root, t)
        pts = _sweep_ticker(records, t, taus)
        if not pts:
            logger.warning("No records for %s under %s; skipping", t, baseline_root)
            continue
        all_points[t] = pts
        # Log the tau=0.5 slice as a quick sanity check
        mid = min(pts, key=lambda q: abs(q.tau - 0.5))
        logger.info("  %s @ tau=0.5: proceed=%d/%d recall=%s tok_saved=%.1f%%",
                    t, mid.n_proceed, mid.n,
                    f"{mid.true_trade_recall*100:.1f}%"
                    if mid.true_trade_recall is not None else "n/a",
                    mid.token_savings_pct * 100)

    # Write JSON
    out_json = out_root / "pareto.json"
    out_json.write_text(json.dumps({
        tkr: [p.to_dict() for p in pts]
        for tkr, pts in all_points.items()
    }, indent=2), encoding="utf-8")
    logger.info("Wrote %s", out_json)

    # Write markdown
    out_md = out_root / "pareto.md"
    out_md.write_text(_render_markdown(all_points), encoding="utf-8")
    logger.info("Wrote %s", out_md)

    # Plot
    _plot_pareto(all_points, out_root / "pareto.png")


if __name__ == "__main__":
    main()
