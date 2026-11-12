"""Kronos live vs stub A/B — is the time-series forecast changing decisions?

Research question
-----------------
The Kronos forecast (argos/data/kronos_client.py) runs on a free-tier
Colab GPU and anchors the multi-horizon forecast node. Setting up and
keeping Kronos alive is one of the paper's biggest ops costs. The
question: *does swapping Kronos for a stub (neutral forecast) change
the committee's final call?*

Arms
----
- **kronos_live** — real Kronos server reachable; multi_horizon_forecast
  carries real probabilities.
- **kronos_stub** — forecast node returns a neutral `{p_up=0.5}` triple
  for 1d / 1w / 1m horizons.

How to produce the arms
-----------------------
    KRONOS_ENABLED=1 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus          # (reference run)
    KRONOS_ENABLED=0 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_kronos_stub
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

DEFAULT_A = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_B = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_kronos_stub"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "ablations" / "kronos_ab"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("kronos_ab")


def _kronos_extras(records: list[dict]) -> dict:
    with_forecast = 0
    p_up_bull = 0
    p_up_bear = 0
    for r in records:
        mh = r.get("multi_horizon_forecast") or {}
        horizons = mh.get("horizons") or {}
        if not horizons:
            continue
        with_forecast += 1
        for h_data in horizons.values():
            if not isinstance(h_data, dict):
                continue
            p = h_data.get("probability_up")
            if not isinstance(p, (int, float)):
                continue
            if p >= 0.55:
                p_up_bull += 1
            elif p <= 0.45:
                p_up_bear += 1
    return {
        "records_with_forecast": with_forecast,
        "horizon_calls_bullish_p55": p_up_bull,
        "horizon_calls_bearish_p45": p_up_bear,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kronos live vs stub A/B")
    p.add_argument("--a", default=str(DEFAULT_A), help="kronos_live root")
    p.add_argument("--b", default=str(DEFAULT_B), help="kronos_stub root")
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
        a_name="kronos_live", b_name="kronos_stub",
        a_extras_fn=_kronos_extras, b_extras_fn=_kronos_extras,
    )

    write_outputs(
        report_root=Path(args.report_root),
        title="Kronos A/B — does the Kronos forecast change decisions?",
        question="Does swapping the Kronos time-series forecast for a neutral stub change the committee's final call?",
        arms_blurb=(
            "full pipeline with the Kronos Colab endpoint live; multi_horizon_forecast carries real probabilities.",
            "forecast node stubbed; every horizon returns p_up = 0.5.",
        ),
        results=results, a_root=a_root, b_root=b_root, tickers=tickers,
        headline="_If kronos_stub preserves recall and accuracy, Kronos isn't earning its ops cost on this universe._",
        extras_cols=("records_with_forecast", "horizon_calls_bullish_p55", "horizon_calls_bearish_p45"),
    )


if __name__ == "__main__":
    main()
