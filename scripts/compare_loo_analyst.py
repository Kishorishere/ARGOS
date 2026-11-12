"""Leave-one-analyst-out — which analyst earns its tokens?

Research question
-----------------
The pipeline has three analysts in parallel: Fundamentals, Sentiment,
Technicals. Each adds a dedicated LLM call. The paper wants one table
that says, per analyst: *if we removed it, how much accuracy do we give
back?* That is the clean causal measurement nobody in the multi-agent
space usually ships.

This script runs **three pairwise comparisons** in one go: the paper's
reference run (all three analysts) against each of three leave-one-out
arms. It emits one combined report with one row per dropped analyst.

Arms
----
- **all_three** — reference run (default `logs/decisions/nasdaq_focus/`)
- **drop_fundamentals** — pipeline with the Fundamentals node stubbed to
  neutral output.
- **drop_sentiment** — pipeline with Sentiment stubbed.
- **drop_technicals** — pipeline with Technicals stubbed.

How to produce the arms
-----------------------
    for a in fundamentals sentiment technicals; do
        DROP_ANALYST=$a python scripts/run_nasdaq_backtest.py \
            --output-root logs/decisions/nasdaq_focus_drop_$a
    done
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env", override=True)

from argos.reports.ablation import compute_pair, write_outputs  # noqa: E402

DEFAULT_FULL = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "ablations" / "loo_analyst"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("loo_analyst")

ANALYSTS = ("fundamentals", "sentiment", "technicals")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Leave-one-analyst-out ablation")
    p.add_argument("--full", default=str(DEFAULT_FULL),
                   help="all_three root (default: reference run)")
    p.add_argument("--drop-dir", default=str(REPO_ROOT / "logs" / "decisions"),
                   help="Directory containing the three drop_<analyst> roots, "
                        "named nasdaq_focus_drop_<analyst>")
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    p.add_argument("--tickers", default=None)
    p.add_argument("--report-root", default=str(DEFAULT_REPORT))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else \
              json.loads(Path(args.universe).read_text())["tickers"]
    full_root = Path(args.full)
    drop_dir = Path(args.drop_dir)

    report_root = Path(args.report_root)
    report_root.mkdir(parents=True, exist_ok=True)

    # For each dropped analyst, run a pairwise comparison and emit a
    # per-arm sub-report. Also assemble an aggregate index.
    index: list[dict] = []
    for a in ANALYSTS:
        b_root = drop_dir / f"nasdaq_focus_drop_{a}"
        logger.info("── drop_%s ── A=%s  B=%s", a, full_root, b_root)
        results = compute_pair(
            tickers, full_root, b_root,
            a_name="all_three", b_name=f"drop_{a}",
        )
        sub_root = report_root / a
        write_outputs(
            report_root=sub_root,
            title=f"Leave-one-out — drop {a}",
            question=f"If we remove the {a} analyst, how much recall and accuracy do we give back?",
            arms_blurb=(
                "full pipeline with all three analysts firing in parallel.",
                f"pipeline with the {a} node stubbed to a neutral signal.",
            ),
            results=results, a_root=full_root, b_root=b_root, tickers=tickers,
            headline=f"_If recall > 95% and h=5 accuracy drops by < 2pp, the {a} analyst is not pulling its weight on this universe._",
        )
        # One-line summary per (ticker, dropped-analyst) for the combined index.
        for r in results:
            index.append({
                "dropped": a,
                "ticker": r.ticker,
                "recall": r.recall.get("recall_vs_a"),
                "a_acc_h5": r.a.dir_acc.get(5),
                "b_acc_h5": r.b.dir_acc.get(5),
                "a_sharpe": r.a.sharpe,
                "b_sharpe": r.b.sharpe,
                "a_tokens": r.a.tokens_total,
                "b_tokens": r.b.tokens_total,
            })

    # Combined index
    (report_root / "index.json").write_text(
        json.dumps(index, indent=2, default=str), encoding="utf-8",
    )
    # Combined markdown table
    lines = ["# Leave-one-analyst-out — combined index", "",
             "One row per (ticker, dropped-analyst). Lower recall = that analyst was shaping real trades.",
             "",
             "| Ticker | Dropped | Recall | h=5 (all) | h=5 (drop) | Sharpe (all) | Sharpe (drop) | Tok all | Tok drop |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in index:
        def _fmt(v, suffix=""):
            return "—" if v is None else (f"{v * 100:.1f}%" if suffix == "%" else
                                           f"{v:+.2f}" if suffix == "+" else f"{v}")
        lines.append("| " + " | ".join([
            row["ticker"], row["dropped"],
            _fmt(row["recall"], "%"),
            _fmt(row["a_acc_h5"], "%"),
            _fmt(row["b_acc_h5"], "%"),
            _fmt(row["a_sharpe"], "+"),
            _fmt(row["b_sharpe"], "+"),
            f"{row['a_tokens']:,}" if row["a_tokens"] is not None else "—",
            f"{row['b_tokens']:,}" if row["b_tokens"] is not None else "—",
        ]) + " |")
    (report_root / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote combined index %s", report_root / "summary.md")


if __name__ == "__main__":
    main()
