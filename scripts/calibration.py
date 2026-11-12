"""Per-agent calibration analysis -- the paper's Figure 3.

For each agent (fundamentals, sentiment, technicals, judge, bull, bear),
read the baseline journals and ask: when an agent said "confidence 0.7"
on a directional call, was the agent correct 70% of the time?

A well-calibrated agent lies on the diagonal of the reliability diagram
(predicted confidence = observed accuracy). An agent whose bar always
sits BELOW the diagonal is *overconfident*; one above is *underconfident*.
An agent whose confidence does not predict correctness at all is decoration.

This script answers the long-standing question "which of our 6 agents
actually earn their tokens?" using only the data already on disk. Zero
new LLM calls.

Outputs:
  reports/calibration/<ticker>_<agent>.png   per (ticker, agent)
  reports/calibration/summary.json            {ticker: {agent: {brier, ece, n}}}
  reports/calibration/summary.md              headline table for paper.tex

Usage:
  python scripts/calibration.py \
      --baseline logs/decisions/nasdaq_focus \
      --universe data/universe/nasdaq_focus.json \
      --out reports/calibration \
      --horizon 5
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
    _load_ticker_records,
    _realized_returns,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("calibration")


AGENTS = ("fundamentals", "sentiment", "technicals", "judge", "bull", "bear")


# ── Extract (confidence, direction) per agent per decision ───────────────


def _analyst_cd(rec: dict, agent: str) -> tuple[Optional[float], Optional[int]]:
    """For fundamentals / sentiment / technicals: (confidence, direction).

    direction: +1 bullish, -1 bearish, None for neutral / missing.
    """
    sig = (rec.get("analyst_signals") or {}).get(agent) or {}
    if not isinstance(sig, dict):
        return None, None
    try:
        c = float(sig.get("confidence") or 0.0)
    except (TypeError, ValueError):
        c = None
    s = (sig.get("signal") or "").lower()
    d = +1 if s == "bullish" else -1 if s == "bearish" else None
    return c, d


def _debate_cd(rec: dict, side: str) -> tuple[Optional[float], Optional[int]]:
    """For bull / bear: (confidence, direction) from debate record."""
    arg = ((rec.get("debate") or {}).get(side)) or {}
    if not isinstance(arg, dict):
        return None, None
    try:
        c = float(arg.get("confidence") or 0.0)
    except (TypeError, ValueError):
        c = None
    pos = (arg.get("position") or "").lower()
    if pos == "buy":
        d = +1
    elif pos == "sell":
        d = -1
    else:
        # Convention fallback -- bull defaults to +1, bear to -1
        d = +1 if side == "bull" else -1
    return c, d


def _judge_cd(rec: dict) -> tuple[Optional[float], Optional[int]]:
    """For judge: (confidence, direction) from blind-judge verdict."""
    jv = rec.get("judge_verdict") or rec.get("judge") or {}
    if not isinstance(jv, dict):
        return None, None
    try:
        c = float(jv.get("confidence") or 0.0)
    except (TypeError, ValueError):
        c = None
    side = (jv.get("winner_side") or "").lower()
    d = +1 if side == "bull" else -1 if side == "bear" else None
    return c, d


def _extract(rec: dict, agent: str) -> tuple[Optional[float], Optional[int]]:
    if agent in ("fundamentals", "sentiment", "technicals"):
        return _analyst_cd(rec, agent)
    if agent == "judge":
        return _judge_cd(rec)
    if agent in ("bull", "bear"):
        return _debate_cd(rec, agent)
    return None, None


# ── Calibration metrics ───────────────────────────────────────────────────


@dataclass
class CalibrationStats:
    agent: str
    ticker: str
    horizon: int
    confidences: list[float] = field(default_factory=list)
    corrects: list[int] = field(default_factory=list)  # 0 or 1

    @property
    def n(self) -> int:
        return len(self.confidences)

    @property
    def brier(self) -> Optional[float]:
        if not self.n:
            return None
        return sum((c - y) ** 2 for c, y in zip(self.confidences, self.corrects)) / self.n

    @property
    def mean_accuracy(self) -> Optional[float]:
        if not self.n:
            return None
        return sum(self.corrects) / self.n

    @property
    def mean_confidence(self) -> Optional[float]:
        if not self.n:
            return None
        return sum(self.confidences) / self.n

    def bins(self, edges: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)
             ) -> list[dict]:
        """Return per-bin {low, high, n, mean_conf, acc} for the reliability plot."""
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            idxs = [i for i, c in enumerate(self.confidences) if lo <= c < hi]
            if not idxs:
                out.append({"low": lo, "high": hi, "n": 0,
                            "mean_conf": None, "acc": None})
                continue
            cs = [self.confidences[i] for i in idxs]
            ys = [self.corrects[i] for i in idxs]
            out.append({
                "low": lo, "high": hi, "n": len(idxs),
                "mean_conf": sum(cs) / len(cs),
                "acc": sum(ys) / len(ys),
            })
        return out

    def ece(self, edges: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01)
            ) -> Optional[float]:
        """Expected calibration error: weighted mean |bin_acc - bin_conf|."""
        if not self.n:
            return None
        total = 0.0
        for b in self.bins(edges):
            if b["n"] == 0:
                continue
            total += (b["n"] / self.n) * abs(b["acc"] - b["mean_conf"])
        return total

    def to_dict(self) -> dict:
        return {
            "agent": self.agent, "ticker": self.ticker, "horizon": self.horizon,
            "n": self.n,
            "brier": None if self.brier is None else round(self.brier, 4),
            "ece": None if self.ece() is None else round(self.ece(), 4),
            "mean_confidence": None if self.mean_confidence is None else round(self.mean_confidence, 4),
            "mean_accuracy": None if self.mean_accuracy is None else round(self.mean_accuracy, 4),
            "bins": self.bins(),
        }


def _compute_agent_stats(records: list[dict], ticker: str, agent: str,
                         horizon: int) -> CalibrationStats:
    good = [r for r in records if not r.get("error") and r.get("date")]
    dates = [r["date"] for r in good]
    realized = _realized_returns(ticker, dates)
    h_idx = HORIZONS.index(horizon)

    stats = CalibrationStats(agent=agent, ticker=ticker, horizon=horizon)
    for rec in good:
        c, d = _extract(rec, agent)
        if c is None or d is None:
            continue
        rets = realized.get(rec["date"]) or []
        if len(rets) <= h_idx:
            continue
        r_h = rets[h_idx]
        if r_h is None or r_h == 0:
            continue
        correct = int((d > 0 and r_h > 0) or (d < 0 and r_h < 0))
        stats.confidences.append(c)
        stats.corrects.append(correct)
    return stats


# ── Rendering ─────────────────────────────────────────────────────────────


def _plot_reliability(all_stats: dict[str, dict[str, CalibrationStats]],
                      out_root: Path) -> None:
    """One PNG per (ticker, agent). Plus one per-ticker summary grid."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; skipping plots")
        return

    out_root.mkdir(parents=True, exist_ok=True)

    # Per-ticker 2x3 grid of reliability diagrams
    for ticker, by_agent in all_stats.items():
        fig, axes = plt.subplots(2, 3, figsize=(12, 7), dpi=110)
        axes = axes.flatten()
        for i, agent in enumerate(AGENTS):
            ax = axes[i]
            s = by_agent.get(agent)
            if s is None or s.n == 0:
                ax.text(0.5, 0.5, f"{agent}\n(no data)",
                        ha="center", va="center", transform=ax.transAxes)
                ax.set_axis_off()
                continue
            bins = s.bins()
            xs = [b["mean_conf"] for b in bins if b["n"] > 0]
            ys = [b["acc"] for b in bins if b["n"] > 0]
            ns = [b["n"] for b in bins if b["n"] > 0]
            # Diagonal reference
            ax.plot([0, 1], [0, 1], color="gray", linestyle="--", linewidth=0.8)
            if xs:
                ax.plot(xs, ys, marker="o", linewidth=1.5)
                # Annotate bin sizes
                for x, y, n in zip(xs, ys, ns):
                    ax.annotate(f"n={n}", (x, y), fontsize=7,
                                xytext=(3, 3), textcoords="offset points")
            ax.set_xlim(0, 1); ax.set_ylim(0, 1)
            brier = s.brier
            ece = s.ece()
            title = f"{agent}  (n={s.n}"
            if brier is not None:
                title += f", Brier={brier:.3f}"
            if ece is not None:
                title += f", ECE={ece:.3f}"
            title += ")"
            ax.set_title(title, fontsize=10)
            ax.set_xlabel("predicted confidence")
            ax.set_ylabel("observed accuracy")
            ax.grid(True, alpha=0.3)
        fig.suptitle(f"{ticker} -- per-agent reliability (h={next(iter(by_agent.values())).horizon})",
                     fontsize=12)
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        out_path = out_root / f"{ticker}_calibration.png"
        fig.savefig(out_path)
        plt.close(fig)
        logger.info("Wrote %s", out_path)


def _render_markdown(all_stats: dict[str, dict[str, CalibrationStats]],
                     horizon: int) -> str:
    L = [f"# Per-agent calibration (h={horizon})", ""]
    L.append("Produced by `scripts/calibration.py` over the baseline "
             "journals. Each row is one (ticker, agent) pair: Brier score "
             "(lower is better; 0.25 = random), ECE (expected calibration "
             "error; lower is better), mean confidence, mean realized "
             "accuracy on directional calls, and sample size.")
    L.append("")
    L.append("| Ticker | Agent | n | Brier | ECE | mean conf | mean acc | gap |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for ticker, by_agent in all_stats.items():
        for agent in AGENTS:
            s = by_agent.get(agent)
            if s is None or s.n == 0:
                L.append(f"| {ticker} | {agent} | 0 | — | — | — | — | — |")
                continue
            brier = s.brier
            ece = s.ece()
            mc = s.mean_confidence
            ma = s.mean_accuracy
            gap = (mc - ma) if (mc is not None and ma is not None) else None
            L.append("| " + " | ".join([
                ticker, agent, str(s.n),
                "—" if brier is None else f"{brier:.3f}",
                "—" if ece is None else f"{ece:.3f}",
                "—" if mc is None else f"{mc:.3f}",
                "—" if ma is None else f"{ma:.3f}",
                "—" if gap is None else f"{gap:+.3f}",
            ]) + " |")
    L.append("")
    L.append("`gap = mean_confidence - mean_accuracy`. Positive = overconfident; "
             "negative = underconfident; near zero = well-calibrated on average.")
    L.append("")
    return "\n".join(L)


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Per-agent calibration analysis")
    p.add_argument("--baseline",
                   default=str(REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"))
    p.add_argument("--universe",
                   default=str(REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"))
    p.add_argument("--tickers", default=None,
                   help="Comma-separated override (default: universe file)")
    p.add_argument("--out", default=str(REPO_ROOT / "reports" / "calibration"))
    p.add_argument("--horizon", type=int, default=5, choices=HORIZONS,
                   help="Realized-return horizon for 'was agent correct' (default 5)")
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

    logger.info("Computing calibration for %d tickers at h=%d",
                len(tickers), args.horizon)

    all_stats: dict[str, dict[str, CalibrationStats]] = {}
    for t in tickers:
        records = _load_ticker_records(baseline_root, t)
        if not records:
            logger.warning("No records for %s under %s", t, baseline_root)
            continue
        by_agent: dict[str, CalibrationStats] = {}
        for agent in AGENTS:
            s = _compute_agent_stats(records, t, agent, args.horizon)
            by_agent[agent] = s
            logger.info("  %s %-12s n=%d brier=%s ece=%s mean_acc=%s",
                        t, agent, s.n,
                        f"{s.brier:.3f}" if s.brier is not None else "n/a",
                        f"{s.ece():.3f}" if s.ece() is not None else "n/a",
                        f"{s.mean_accuracy:.3f}" if s.mean_accuracy is not None else "n/a")
        all_stats[t] = by_agent

    # Write JSON
    out_json = out_root / "summary.json"
    out_json.write_text(json.dumps({
        tkr: {a: s.to_dict() for a, s in by_agent.items()}
        for tkr, by_agent in all_stats.items()
    }, indent=2), encoding="utf-8")
    logger.info("Wrote %s", out_json)

    # Write markdown
    out_md = out_root / "summary.md"
    out_md.write_text(_render_markdown(all_stats, args.horizon), encoding="utf-8")
    logger.info("Wrote %s", out_md)

    # Plots
    _plot_reliability(all_stats, out_root)


if __name__ == "__main__":
    main()
