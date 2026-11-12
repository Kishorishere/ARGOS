"""Gate without triggers vs gate WITH triggers — the Phase B promise.

Research question
-----------------
In Phase B we built `argos/triggers/` — a trigger library that observes
reasons the system should wake up (RSI cross, Donchian break, earnings
proximity, …). The event loop uses them; the Edge Gate does not — yet.
Rewriting the gate prompt to read triggers needs a paired ablation to
land in the paper cleanly, which is what this script is for.

Arms
----
- **gate_blind**  — the current gated run; gate decides proceed/skip
  from analyst signals + indicators + forecast, no triggers in prompt.
- **gate_triggers** — the gate prompt is extended to receive the fired
  triggers for that (ticker, as_of). Everything else identical.

The research claim to test: *when triggers are visible to the gate, does
recall-vs-baseline go up (the gate stops skipping real trade days) and/or
does skip rate change in a sensible direction?*

How to produce the arms
-----------------------
    # gate_blind is the Phase A gated run:
    GATE_READS_TRIGGERS=0 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_gated

    # gate_triggers — same backtest with the new flag:
    GATE_READS_TRIGGERS=1 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_gated_triggers

Both arms require the prompt change in `prompts/edge_gate.md` to be in
place before kick-off; that prompt change is the content of the PR this
ablation accompanies. The ablation table is the merge-gate for that PR.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(REPO_ROOT / ".env", override=True)

from argos.reports.ablation import compute_pair, write_outputs  # noqa: E402

DEFAULT_A = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_gated"
DEFAULT_B = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_gated_triggers"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "ablations" / "gate_with_triggers"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gate_with_triggers")


def _gate_extras(records: list[dict]) -> dict:
    present = skips = 0
    confidences: list[float] = []
    trigger_kinds = Counter()
    records_with_triggers = 0
    for r in records:
        gv = r.get("gate_verdict")
        if isinstance(gv, dict):
            present += 1
            if gv.get("proceed") is False:
                skips += 1
            if isinstance(gv.get("confidence"), (int, float)):
                confidences.append(float(gv["confidence"]))
        trigs = r.get("triggers") or []
        if trigs:
            records_with_triggers += 1
            for t in trigs:
                trigger_kinds[str((t or {}).get("kind") or "?")] += 1
    return {
        "gate_skip_rate": round(skips / present, 3) if present else "—",
        "gate_mean_confidence": round(statistics.mean(confidences), 3) if confidences else "—",
        "records_with_triggers": records_with_triggers,
        "top_trigger_kinds": dict(trigger_kinds.most_common(5)),
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gate blind vs gate-with-triggers A/B")
    p.add_argument("--a", default=str(DEFAULT_A), help="gate_blind root")
    p.add_argument("--b", default=str(DEFAULT_B), help="gate_triggers root")
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    p.add_argument("--tickers", default=None)
    p.add_argument("--report-root", default=str(DEFAULT_REPORT))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else \
              json.loads(Path(args.universe).read_text())["tickers"]
    a_root, b_root = Path(args.a), Path(args.b)

    results = compute_pair(
        tickers, a_root, b_root,
        a_name="gate_blind", b_name="gate_triggers",
        a_extras_fn=_gate_extras, b_extras_fn=_gate_extras,
    )

    write_outputs(
        report_root=Path(args.report_root),
        title="Gate blind vs gate-with-triggers — the Phase B promise",
        question="When the edge gate can see the fired triggers for a date, does its "
                 "proceed/skip decision improve recall of real trade days?",
        arms_blurb=(
            "edge gate reads analyst signals + indicator snapshot + forecast, no triggers.",
            "edge gate reads the same inputs + the day's fired triggers (from argos.triggers.check_all).",
        ),
        results=results, a_root=a_root, b_root=b_root, tickers=tickers,
        headline=("_The claim to merge: giving the gate visibility into event triggers "
                  "raises recall vs the paper's baseline without materially raising skip "
                  "rate on quiet days. If recall doesn't move, the gate was already picking "
                  "up the same signal implicitly through indicators._"),
        extras_cols=("gate_skip_rate", "gate_mean_confidence", "records_with_triggers"),
    )


if __name__ == "__main__":
    main()
