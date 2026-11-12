"""Walk-forward calibration report CLI.

Reads the reference run's journals, computes a prior-only calibration
curve per (ticker, date), scores the raw-vs-walk-forward Brier gap and
the selective-prediction top-quartile accuracy. Writes to
reports/walk_forward/.

Zero LLM calls. Pure re-scoring of existing journal data.

Usage:
    python scripts/eval_walk_forward.py --horizon 5
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

from argos.reports.walk_forward import render_markdown, score_walk_forward  # noqa: E402

DEFAULT_ROOT = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_REPORT = REPO_ROOT / "reports" / "walk_forward"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("eval_walk_forward")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward calibration report")
    p.add_argument("--journal-root", default=str(DEFAULT_ROOT),
                   help="Reference run root (default: logs/decisions/nasdaq_focus)")
    p.add_argument("--horizon", type=int, default=5, help="Score horizon (1 / 5 / 20)")
    p.add_argument("--min-prior", type=int, default=3,
                   help="Minimum prior samples in a bin before it can produce a calibrated output")
    p.add_argument("--report-root", default=str(DEFAULT_REPORT))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    root = Path(args.journal_root)
    report_root = Path(args.report_root)
    report_root.mkdir(parents=True, exist_ok=True)

    logger.info("walk-forward scoring: root=%s horizon=%d min_prior=%d",
                root, args.horizon, args.min_prior)
    result = score_walk_forward(root, horizon=args.horizon, min_prior=args.min_prior)
    logger.info("scored %d points across %d tickers",
                result["aggregate"]["n_scored"], result["aggregate"]["n_tickers"])

    (report_root / "summary.md").write_text(render_markdown(result), encoding="utf-8")
    (report_root / "summary.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "points"}, indent=2, default=str),
        encoding="utf-8",
    )
    (report_root / "points.jsonl").write_text(
        "\n".join(json.dumps(p, default=str) for p in result["points"]),
        encoding="utf-8",
    )
    logger.info("Wrote %s", report_root / "summary.md")
    logger.info("Wrote %s", report_root / "summary.json")
    logger.info("Wrote %s", report_root / "points.jsonl")


if __name__ == "__main__":
    main()
