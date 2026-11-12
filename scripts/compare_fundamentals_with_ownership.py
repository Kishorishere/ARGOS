"""Fundamentals analyst blind vs with-insider-form4.

Research question
-----------------
Phase C added an Ownership panel sourced from EDGAR Form 4 insider
transactions. The Fundamentals analyst — the one agent that already
reasons over ownership-shaped signals — doesn't yet see Form 4 in its
prompt. This ablation gates that prompt change.

Arms
----
- **fund_blind**     — Fundamentals analyst prompt unchanged.
- **fund_with_own**  — Fundamentals prompt extended with a short block
  summarizing recent insider buys/sells (aggregate USD and top actor).

How to produce the arms
-----------------------
    # fund_blind — the reference run:
    # logs/decisions/nasdaq_focus/

    # fund_with_own:
    FUNDAMENTALS_READS_OWNERSHIP=1 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_fund_own

**Merge rule.** Ship if directional accuracy at h=20 rises by ≥ 1pp
AND recall ≥ 95% — insider activity is a longer-horizon signal, so h=20
is where it should pay rent, not h=1.
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

DEFAULT_A = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_B = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_fund_own"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "ablations" / "fundamentals_with_ownership"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fundamentals_with_ownership")


def _fund_extras(records: list[dict]) -> dict:
    signals = Counter()
    confs: list[float] = []
    cited_ownership = 0
    for r in records:
        sig = ((r.get("analyst_signals") or {}).get("fundamentals")) or {}
        s = sig.get("signal")
        if isinstance(s, str):
            signals[s.lower()] += 1
        c = sig.get("confidence")
        if isinstance(c, (int, float)):
            confs.append(float(c))
        # Heuristic audit: did the agent cite insider/13F/ownership?
        factors = sig.get("key_factors") or []
        text = " ".join(str(f) for f in factors) + " " + str(sig.get("reasoning") or "")
        if any(t in text.lower() for t in ("insider", "form 4", "13f", "institutional", "ownership")):
            cited_ownership += 1
    return {
        "n_bullish": signals.get("bullish", 0),
        "n_bearish": signals.get("bearish", 0),
        "n_neutral": signals.get("neutral", 0),
        "mean_fund_conf": round(statistics.mean(confs), 3) if confs else "—",
        "records_citing_ownership": cited_ownership,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fundamentals prompt blind vs with-ownership A/B")
    p.add_argument("--a", default=str(DEFAULT_A))
    p.add_argument("--b", default=str(DEFAULT_B))
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
        a_name="fund_blind", b_name="fund_with_own",
        a_extras_fn=_fund_extras, b_extras_fn=_fund_extras,
    )

    write_outputs(
        report_root=Path(args.report_root),
        title="Fundamentals prompt A/B — blind vs with-ownership",
        question="Does giving the Fundamentals analyst recent insider Form 4 context change the committee's final call, especially at h=20?",
        arms_blurb=(
            "Fundamentals reads cached fundamentals only.",
            "Fundamentals additionally receives a short insider-Form-4 block (aggregate USD, direction, top actor).",
        ),
        results=results, a_root=a_root, b_root=b_root, tickers=tickers,
        headline="_Merge rule: h=20 accuracy rises ≥ 1pp AND recall ≥ 95%. Insider activity is a longer-horizon signal; h=1 is the wrong place to look for its contribution._",
        extras_cols=("n_bullish", "n_bearish", "n_neutral", "mean_fund_conf", "records_citing_ownership"),
    )


if __name__ == "__main__":
    main()
