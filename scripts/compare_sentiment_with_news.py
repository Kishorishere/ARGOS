"""Sentiment analyst blind vs sentiment analyst reading Finnhub news.

Research question
-----------------
Phase C wired real news into the Sentiment panel's raw layer, but the
Sentiment analyst's *prompt* is still blind to it — same shape as the
gate-with-triggers situation. This ablation gates the prompt change
that would hand the Sentiment analyst the day's recent news items.

Arms
----
- **sent_blind**     — Sentiment analyst prompt unchanged (current ref run).
- **sent_with_news** — Sentiment prompt extended to receive the last 24h
  of Finnhub items; everything else identical.

How to produce the arms
-----------------------
    # sent_blind — the paper's reference run already lives here:
    # logs/decisions/nasdaq_focus/

    # sent_with_news — needs the prompt change in prompts/sentiment_analyst.md
    # plus an env flag the node checks before injecting the news block.
    SENTIMENT_READS_NEWS=1 python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_sent_news

**Merge rule.** If recall_vs_a <= 97% or h=5 directional accuracy drops by
>=1pp, the prompt change doesn't ship — real news feeds often *distract*
LLMs with volume rather than inform them, and we'd rather keep the
blind-but-steady baseline than ship a noisy improvement.
"""
from __future__ import annotations

import argparse
import json
import logging
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
DEFAULT_B = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_sent_news"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "ablations" / "sentiment_with_news"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("sentiment_with_news")


def _sentiment_extras(records: list[dict]) -> dict:
    """Pull the sentiment analyst's signal distribution so a prompt
    change is visible beyond just final PM action deltas."""
    signals = Counter()
    confs: list[float] = []
    n_with_news = 0
    for r in records:
        sig = ((r.get("analyst_signals") or {}).get("sentiment")) or {}
        s = sig.get("signal")
        if isinstance(s, str):
            signals[s.lower()] += 1
        c = sig.get("confidence")
        if isinstance(c, (int, float)):
            confs.append(float(c))
        # records_with_news populates once the SENTIMENT_READS_NEWS arm
        # ships; it's the audit trail that the prompt actually saw news.
        if r.get("news_items"):
            n_with_news += 1
    return {
        "n_bullish": signals.get("bullish", 0),
        "n_bearish": signals.get("bearish", 0),
        "n_neutral": signals.get("neutral", 0),
        "mean_sentiment_conf": (
            round(sum(confs) / len(confs), 3) if confs else "—"
        ),
        "records_with_news_block": n_with_news,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sentiment prompt blind vs with-news A/B")
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
        a_name="sent_blind", b_name="sent_with_news",
        a_extras_fn=_sentiment_extras, b_extras_fn=_sentiment_extras,
    )

    write_outputs(
        report_root=Path(args.report_root),
        title="Sentiment prompt A/B — blind vs with-news",
        question="Does giving the Sentiment analyst the day's Finnhub news items change what it says and what the committee ships?",
        arms_blurb=(
            "Sentiment analyst reads the free-text inputs it always has; news feed is NOT in the prompt.",
            "Sentiment analyst additionally receives the last 24h of material Finnhub items.",
        ),
        results=results, a_root=a_root, b_root=b_root, tickers=tickers,
        headline="_Merge rule: recall ≥ 97% AND no h=5 accuracy regression ≥ 1pp. News feeds frequently distract LLMs with volume; we only ship if the signal survives that._",
        extras_cols=("n_bullish", "n_bearish", "n_neutral", "mean_sentiment_conf", "records_with_news_block"),
    )


if __name__ == "__main__":
    main()
