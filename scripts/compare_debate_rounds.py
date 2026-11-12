"""Debate depth A/B — does round 2 of the bull/bear debate earn its tokens?

Research question
-----------------
The debate loop runs N rounds (default 2). Each additional round doubles
the LLM cost of the debate stage. Is the second round changing the final
PM action, or is it echoing round 1?

Arms
----
- **rounds_1** — single-pass debate; bull speaks once, bear responds, done.
- **rounds_2** — the paper's default; bull → bear → bull → bear.

How to produce the arms
-----------------------
    MAX_DEBATE_ROUNDS=1 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_rounds1

    MAX_DEBATE_ROUNDS=2 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus   # (the reference run)

Useful extra: number of rounds *actually completed* per record (taken
from `debate.rounds_completed` in the journal).
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

DEFAULT_A = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_rounds1"
DEFAULT_B = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "ablations" / "debate_rounds"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("debate_rounds")


def _debate_extras(records: list[dict]) -> dict:
    rounds_completed: list[int] = []
    bull_confs: list[float] = []
    bear_confs: list[float] = []
    for r in records:
        d = r.get("debate") or {}
        rc = d.get("rounds_completed")
        if isinstance(rc, int):
            rounds_completed.append(rc)
        bull = d.get("bull") or {}
        bear = d.get("bear") or {}
        if isinstance(bull.get("confidence"), (int, float)):
            bull_confs.append(float(bull["confidence"]))
        if isinstance(bear.get("confidence"), (int, float)):
            bear_confs.append(float(bear["confidence"]))
    return {
        "mean_rounds_completed": round(statistics.mean(rounds_completed), 2) if rounds_completed else "—",
        "max_rounds_completed": max(rounds_completed) if rounds_completed else "—",
        "mean_bull_conf": round(statistics.mean(bull_confs), 3) if bull_confs else "—",
        "mean_bear_conf": round(statistics.mean(bear_confs), 3) if bear_confs else "—",
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debate single-pass vs multi-round A/B")
    p.add_argument("--a", default=str(DEFAULT_A), help="single-pass root")
    p.add_argument("--b", default=str(DEFAULT_B), help="multi-round root (default: reference run)")
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    p.add_argument("--tickers", default=None)
    p.add_argument("--report-root", default=str(DEFAULT_REPORT))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else \
              json.loads(Path(args.universe).read_text())["tickers"]
    a_root, b_root = Path(args.a), Path(args.b)
    logger.info("rounds_1 (A): %s", a_root)
    logger.info("rounds_2 (B): %s", b_root)

    results = compute_pair(
        tickers, a_root, b_root,
        a_name="rounds_1", b_name="rounds_2",
        a_extras_fn=_debate_extras, b_extras_fn=_debate_extras,
    )

    write_outputs(
        report_root=Path(args.report_root),
        title="Debate depth A/B — single-pass vs two-round",
        question="Does the second round of the bull/bear debate change the committee's final call?",
        arms_blurb=(
            "bull → bear, one exchange, straight to the judge.",
            "bull → bear → bull → bear (the paper's default).",
        ),
        results=results, a_root=a_root, b_root=b_root, tickers=tickers,
        headline="_If recall between rounds_1 and rounds_2 is very high and accuracy is comparable, the second round is dead weight tokens._",
        extras_cols=("mean_rounds_completed", "max_rounds_completed", "mean_bull_conf", "mean_bear_conf"),
    )


if __name__ == "__main__":
    main()
