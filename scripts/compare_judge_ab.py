"""Judge-off vs judge-on A/B — does the blind judge matter?

Research question
-----------------
The blind judge (`argos/nodes/judge.py`) reads the bull/bear transcript
with side labels stripped and scores which side argued better. It is
cheap (one LLM call per decision) but adds a call to every non-gated
decision. The question is: *does the final Portfolio Manager action
change when the judge is present, and does it preserve or improve
downstream directional accuracy?*

Arms
----
- **judge_off** — pipeline runs normally; `judge_node` short-circuits
  (either skipped in the graph or stubbed to a neutral tie result).
  Journal root defaults to `logs/decisions/nasdaq_focus_judge_off/`.
- **judge_on**  — judge runs. Journal root defaults to
  `logs/decisions/nasdaq_focus/` (the paper's reference run already
  has the judge on).

How to produce the arms
-----------------------
    # judge-off arm: set env, re-run the backtest harness to a parallel dir.
    JUDGE_ENABLED=0  python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_judge_off

    # judge-on arm: the paper's reference run already lives at
    # logs/decisions/nasdaq_focus/. Use it as `--a`.

Journal records expose `judge_verdict` on the judge_on arm; this script
reads it to compute agreement rate with the final PM decision.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env", override=True)

from argos.reports.ablation import compute_pair, write_outputs  # noqa: E402

DEFAULT_A = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_B = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_judge_off"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "ablations" / "judge_ab"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("judge_ab")


def _judge_extras(records: list[dict]) -> dict:
    """Judge-on arm metrics: how often does the judge pick the side the
    PM eventually acted on? High agreement = the judge is already baked
    into the committee's logic; low = the judge contributes an
    independent signal that disagrees with the final call."""
    agree = disagree = present = 0
    confidences: list[float] = []
    for r in records:
        jv = r.get("judge_verdict")
        if not isinstance(jv, dict):
            continue
        present += 1
        if isinstance(jv.get("confidence"), (int, float)):
            confidences.append(float(jv["confidence"]))
        winner = (jv.get("winner_side") or "").lower()
        action = ((r.get("final_decision") or {}).get("action") or "").lower()
        aligned = (winner == "bull" and action in ("buy", "long")) or \
                  (winner == "bear" and action in ("sell", "short"))
        if winner in ("bull", "bear") and action in ("buy", "sell", "long", "short", "hold"):
            if aligned:
                agree += 1
            else:
                disagree += 1
    return {
        "judge_present": present,
        "judge_agrees_pm": agree,
        "judge_disagrees_pm": disagree,
        "judge_pm_agreement_rate": round(agree / (agree + disagree), 3) if (agree + disagree) else "—",
        "judge_mean_confidence": round(statistics.mean(confidences), 3) if confidences else "—",
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Judge-on vs judge-off A/B")
    p.add_argument("--a", default=str(DEFAULT_A), help="judge_on root (default: reference run)")
    p.add_argument("--b", default=str(DEFAULT_B), help="judge_off root")
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    p.add_argument("--tickers", default=None, help="Comma-separated (default: universe file)")
    p.add_argument("--report-root", default=str(DEFAULT_REPORT))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else \
              json.loads(Path(args.universe).read_text())["tickers"]
    a_root, b_root = Path(args.a), Path(args.b)
    logger.info("judge_on  (A): %s", a_root)
    logger.info("judge_off (B): %s", b_root)
    logger.info("Tickers:       %s", tickers)

    results = compute_pair(
        tickers, a_root, b_root,
        a_name="judge_on", b_name="judge_off",
        a_extras_fn=_judge_extras,
    )

    write_outputs(
        report_root=Path(args.report_root),
        title="Judge A/B — does the blind judge change the PM's final call?",
        question="Does the blind judge change the committee's final decision enough to matter, "
                 "and does it preserve directional accuracy?",
        arms_blurb=(
            "full pipeline including the blind judge (one LLM call reading the debate transcript with side labels hidden).",
            "pipeline with the judge node disabled; bull/bear transcript flows straight to the risk manager.",
        ),
        results=results, a_root=a_root, b_root=b_root, tickers=tickers,
        headline="_If judge_off preserves accuracy and Sharpe at a meaningful token saving, the judge is a stylistic affectation, not a research contribution._",
        extras_cols=("judge_pm_agreement_rate", "judge_mean_confidence", "judge_present"),
    )


if __name__ == "__main__":
    main()
