"""Gate-off vs gate-on A/B comparison — the paper's main result table.

Reads two journal roots (baseline vs gated) that cover the SAME tickers and
decision dates, and produces the research table that answers the paper's
core question:

    "Does the pre-debate edge gate preserve decision quality while cutting
     token cost?"

Columns per ticker (and aggregate):
  • tokens: total tokens consumed over the backtest window (gate_off vs on)
  • tokens/decision: average per-decision cost
  • decision mix: n_hold, n_buy, n_sell
  • realized: Sharpe, hit rate, PnL
  • directional accuracy: h=1, 5, 20 on final decision
  • TRUE-TRADE RECALL (headline research metric): of the dates where the
    baseline (gate_off) issued BUY or SELL, what fraction did gate_on ALSO
    issue BUY or SELL on (i.e. did NOT suppress). A gate that preserves 95%+
    of true trading days is "safe"; below 90% means the gate is cutting
    into real alpha and needs loosening.
  • Gate behavior: skip rate, most-frequent proceed triggers, mean gate
    confidence.

Writes:
  reports/gate_ab/summary.md        human-readable research table
  reports/gate_ab/summary.json      machine-readable (downstream plots)
  reports/gate_ab/per_decision.csv  one row per (ticker, date) with both
                                    runs' final actions side-by-side

Usage:
  python scripts/compare_gate_ab.py \
      --baseline logs/decisions/nasdaq_focus \
      --gated    logs/decisions/nasdaq_focus_gated \
      --universe data/universe/nasdaq_focus.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env", override=True)

# Reuse the eval harness's loaders / scoring — single source of truth.
from scripts.eval_nasdaq import (  # noqa: E402
    HORIZONS,
    _aggregate_tokens,
    _confidence_gap,
    _direction_from_action,
    _load_ticker_records,
    _paper_nav,
    _realized_returns,
    _score_directional,
    _sharpe_sortino,
    _max_drawdown,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gate_ab")

DEFAULT_BASELINE = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_GATED = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus_gated"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"
DEFAULT_REPORT_ROOT = REPO_ROOT / "reports" / "gate_ab"


# ── Metric computation ────────────────────────────────────────────────────


@dataclass
class ArmStats:
    """Stats for one arm (baseline OR gated) on one ticker."""
    arm: str
    ticker: str
    n_records: int = 0
    n_hold: int = 0
    n_buy: int = 0
    n_sell: int = 0
    tokens_total: int = 0
    tokens_per_decision: Optional[float] = None
    sharpe: Optional[float] = None
    hit_rate_pct: Optional[float] = None
    pnl_usd: Optional[float] = None
    return_pct: Optional[float] = None
    max_dd: Optional[float] = None
    dir_acc: dict = field(default_factory=dict)  # {h: accuracy}

    # Gated arm only
    gate_skip_rate: Optional[float] = None
    gate_mean_confidence: Optional[float] = None
    gate_trigger_counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "arm": self.arm, "ticker": self.ticker,
            "n_records": self.n_records,
            "n_hold": self.n_hold, "n_buy": self.n_buy, "n_sell": self.n_sell,
            "tokens_total": self.tokens_total,
            "tokens_per_decision": self.tokens_per_decision,
            "sharpe": self.sharpe,
            "hit_rate_pct": self.hit_rate_pct,
            "pnl_usd": self.pnl_usd,
            "return_pct": self.return_pct,
            "max_dd": self.max_dd,
            "dir_acc": self.dir_acc,
            "gate_skip_rate": self.gate_skip_rate,
            "gate_mean_confidence": self.gate_mean_confidence,
            "gate_trigger_counts": self.gate_trigger_counts,
        }


def _count_decisions(records: list[dict]) -> tuple[int, int, int]:
    """Count HOLD / BUY / SELL across records (case-insensitive, 'long'/'short' aliased)."""
    n_hold = n_buy = n_sell = 0
    for r in records:
        action = ((r.get("final_decision") or {}).get("action") or "").lower()
        if action in ("buy", "long"):
            n_buy += 1
        elif action in ("sell", "short"):
            n_sell += 1
        else:
            n_hold += 1
    return n_hold, n_buy, n_sell


def _gate_behavior(records: list[dict]) -> tuple[Optional[float], Optional[float], dict]:
    """For the gated arm: skip rate, mean gate confidence, trigger frequency."""
    skips = 0
    present = 0
    confidences: list[float] = []
    triggers = Counter()
    for r in records:
        gv = r.get("gate_verdict")
        if not isinstance(gv, dict):
            continue
        present += 1
        if gv.get("proceed") is False:
            skips += 1
        conf = gv.get("confidence")
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))
        for t in gv.get("triggers") or []:
            triggers[str(t)] += 1
    if not present:
        return None, None, {}
    skip_rate = skips / present
    mean_conf = statistics.mean(confidences) if confidences else None
    return skip_rate, mean_conf, dict(triggers.most_common(10))


def _analyze_arm(arm: str, journal_root: Path, ticker: str) -> ArmStats:
    stats = ArmStats(arm=arm, ticker=ticker)
    records = _load_ticker_records(journal_root, ticker)
    good = [r for r in records if not r.get("error") and r.get("date")]
    stats.n_records = len(good)
    if not good:
        return stats

    stats.n_hold, stats.n_buy, stats.n_sell = _count_decisions(good)

    tokens_agg = _aggregate_tokens(good)
    stats.tokens_total = int((tokens_agg.get("totals") or {}).get("total_tokens") or 0)
    if good:
        stats.tokens_per_decision = stats.tokens_total / len(good)

    dates = [r["date"] for r in good]
    realized_map = _realized_returns(ticker, dates)
    realized_by_h = [realized_map.get(d, [None] * len(HORIZONS)) for d in dates]
    final_dirs = [_direction_from_action((r.get("final_decision") or {}).get("action")) for r in good]
    scored = _score_directional(final_dirs, realized_by_h)
    stats.dir_acc = {h: (s.accuracy if s.accuracy is not None else None) for h, s in scored.items()}

    nav, period_returns, sim_stats = _paper_nav(journal_root, ticker)
    if period_returns:
        sharpe, _ = _sharpe_sortino(period_returns)
        stats.sharpe = sharpe
        stats.max_dd = _max_drawdown(nav)
    if sim_stats:
        stats.hit_rate_pct = sim_stats.get("hit_rate_pct")
        stats.return_pct = sim_stats.get("return_pct")
        if nav and nav[0]:
            stats.pnl_usd = nav[-1] - nav[0]

    if arm == "gated":
        skip_rate, mean_conf, trig = _gate_behavior(good)
        stats.gate_skip_rate = skip_rate
        stats.gate_mean_confidence = mean_conf
        stats.gate_trigger_counts = trig

    return stats


def _true_trade_recall(baseline_records: list[dict], gated_records: list[dict]) -> dict:
    """Headline research metric.

    Of the dates where the baseline arm issued a directional (BUY/SELL)
    decision, what fraction did the gated arm ALSO issue a directional
    decision on? High recall = the gate preserves alpha. Low recall = the
    gate is cutting into real trades.

    Also reports the inverse: of gated-arm trades, what fraction were ALSO
    baseline trades (precision — how often the gate agrees it was a trade
    day when the baseline agreed).
    """
    def _by_date(records: list[dict]) -> dict[str, str]:
        return {r["date"]: ((r.get("final_decision") or {}).get("action") or "").lower()
                for r in records if r.get("date")}

    base_by_date = _by_date(baseline_records)
    gated_by_date = _by_date(gated_records)
    common_dates = sorted(set(base_by_date) & set(gated_by_date))

    base_trades = {d for d in common_dates if base_by_date[d] in ("buy", "sell", "long", "short")}
    gated_trades = {d for d in common_dates if gated_by_date[d] in ("buy", "sell", "long", "short")}

    tp = len(base_trades & gated_trades)
    fn_recall_denom = len(base_trades)
    fp_prec_denom = len(gated_trades)

    recall = tp / fn_recall_denom if fn_recall_denom else None
    precision = tp / fp_prec_denom if fp_prec_denom else None

    base_only = sorted(base_trades - gated_trades)
    gated_only = sorted(gated_trades - base_trades)

    return {
        "n_common_dates": len(common_dates),
        "baseline_trade_days": fn_recall_denom,
        "gated_trade_days": fp_prec_denom,
        "both_trade_days": tp,
        "recall_vs_baseline": recall,       # paper headline
        "precision_vs_baseline": precision,
        "baseline_only_trade_dates": base_only,
        "gated_only_trade_dates": gated_only,
    }


# ── Rendering ─────────────────────────────────────────────────────────────


def _fmt_int(v) -> str:
    return "—" if v is None else f"{int(v):,}"


def _fmt_float(v, pct: bool = False, sig: int = 2) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:+.{sig}f}%"
    return f"{v:+.{sig}f}"


def _fmt_dir_acc(acc: dict, h: int) -> str:
    v = acc.get(h)
    return "—" if v is None else f"{v * 100:.1f}%"


def _render_markdown(results: list[dict]) -> str:
    L = ["# Gate A/B — pre-debate edge gate research summary", ""]
    L.append("Research question: _does a cheap pre-debate gate preserve decision quality while cutting token cost?_")
    L.append("")
    L.append("Arms:")
    L.append("- **baseline** — full pipeline every decision (analysts → forecast → bull/bear → judge → risk → PM)")
    L.append("- **gated** — same pipeline + pre-debate edge gate (one flash-lite call that may short-circuit to HOLD and skip bull/bear/judge/risk/PM)")
    L.append("")

    # Headline cost table
    L.append("## Cost (tokens)")
    L.append("| Ticker | Baseline tokens | Gated tokens | Δ tokens | Δ% | Baseline tok/dec | Gated tok/dec |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        b, g = r["baseline"], r["gated"]
        delta = g["tokens_total"] - b["tokens_total"]
        delta_pct = (delta / b["tokens_total"] * 100) if b["tokens_total"] else None
        L.append("| " + " | ".join([
            r["ticker"],
            _fmt_int(b["tokens_total"]), _fmt_int(g["tokens_total"]),
            _fmt_int(delta),
            "—" if delta_pct is None else f"{delta_pct:+.1f}%",
            _fmt_int(b["tokens_per_decision"]),
            _fmt_int(g["tokens_per_decision"]),
        ]) + " |")
    L.append("")

    # Decision mix
    L.append("## Decision mix")
    L.append("| Ticker | Arm | n | HOLD | BUY | SELL | HOLD% |")
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for r in results:
        for arm in ("baseline", "gated"):
            a = r[arm]
            n = a["n_records"]
            hold_pct = (a["n_hold"] / n * 100) if n else None
            L.append("| " + " | ".join([
                r["ticker"], arm, str(n),
                str(a["n_hold"]), str(a["n_buy"]), str(a["n_sell"]),
                "—" if hold_pct is None else f"{hold_pct:.1f}%",
            ]) + " |")
    L.append("")

    # Directional accuracy preservation
    L.append("## Directional accuracy — final decision")
    L.append("Does gating damage downstream quality? If gated accuracy drops much more than the trade count, the gate is suppressing information.")
    L.append("")
    L.append("| Ticker | Arm | h=1 | h=5 | h=20 |")
    L.append("|---|---|---:|---:|---:|")
    for r in results:
        for arm in ("baseline", "gated"):
            a = r[arm]
            L.append("| " + " | ".join([
                r["ticker"], arm,
                _fmt_dir_acc(a["dir_acc"], 1),
                _fmt_dir_acc(a["dir_acc"], 5),
                _fmt_dir_acc(a["dir_acc"], 20),
            ]) + " |")
    L.append("")

    # Realized returns
    L.append("## Realized paper P&L")
    L.append("| Ticker | Arm | Sharpe | Hit rate | PnL (USD) | Max DD |")
    L.append("|---|---|---:|---:|---:|---:|")
    for r in results:
        for arm in ("baseline", "gated"):
            a = r[arm]
            L.append("| " + " | ".join([
                r["ticker"], arm,
                _fmt_float(a["sharpe"]),
                "—" if a["hit_rate_pct"] is None else f"{a['hit_rate_pct']:.1f}%",
                "—" if a["pnl_usd"] is None else f"{a['pnl_usd']:+.2f}",
                _fmt_float(a["max_dd"], pct=True),
            ]) + " |")
    L.append("")

    # Headline recall table
    L.append("## True-trade recall (headline research metric)")
    L.append("Of the dates where the **baseline** issued a BUY/SELL, what fraction did the **gated** arm also issue a BUY/SELL on?")
    L.append("- recall = 1.0 → the gate skipped only days the baseline would have held anyway (lossless)")
    L.append("- recall < 0.9 → the gate is cutting into real trades; loosen the skip criteria")
    L.append("")
    L.append("| Ticker | Common days | Baseline trades | Gated trades | Both | Recall | Precision |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        rc = r["recall"]
        L.append("| " + " | ".join([
            r["ticker"],
            str(rc["n_common_dates"]),
            str(rc["baseline_trade_days"]),
            str(rc["gated_trade_days"]),
            str(rc["both_trade_days"]),
            _fmt_float(rc["recall_vs_baseline"], pct=True, sig=1),
            _fmt_float(rc["precision_vs_baseline"], pct=True, sig=1),
        ]) + " |")
    L.append("")

    # Gate behavior
    L.append("## Gate behavior (gated arm only)")
    L.append("| Ticker | Skip rate | Mean gate conf | Top proceed-triggers |")
    L.append("|---|---:|---:|---|")
    for r in results:
        g = r["gated"]
        trig = g.get("gate_trigger_counts") or {}
        trig_str = ", ".join(f"{k}:{v}" for k, v in list(trig.items())[:5]) or "—"
        L.append("| " + " | ".join([
            r["ticker"],
            _fmt_float(g.get("gate_skip_rate"), pct=True, sig=1),
            _fmt_float(g.get("gate_mean_confidence"), sig=2),
            trig_str,
        ]) + " |")
    L.append("")

    return "\n".join(L)


def _write_per_decision_csv(out_path: Path, baseline_root: Path,
                            gated_root: Path, tickers: list[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "date", "baseline_action", "gated_action",
                    "gate_proceed", "gate_confidence", "gate_triggers",
                    "baseline_tokens", "gated_tokens"])
        for t in tickers:
            base = {r["date"]: r for r in _load_ticker_records(baseline_root, t)
                    if r.get("date")}
            gated = {r["date"]: r for r in _load_ticker_records(gated_root, t)
                     if r.get("date")}
            dates = sorted(set(base) | set(gated))
            for d in dates:
                b = base.get(d, {}) or {}
                g = gated.get(d, {}) or {}
                gv = g.get("gate_verdict") or {}
                w.writerow([
                    t, d,
                    (b.get("final_decision") or {}).get("action") or "",
                    (g.get("final_decision") or {}).get("action") or "",
                    gv.get("proceed", ""),
                    gv.get("confidence", ""),
                    ";".join(gv.get("triggers") or []),
                    ((b.get("token_usage") or {}).get("totals") or {}).get("total_tokens", ""),
                    ((g.get("token_usage") or {}).get("totals") or {}).get("total_tokens", ""),
                ])


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gate-off vs gate-on A/B research table")
    p.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    p.add_argument("--gated", default=str(DEFAULT_GATED))
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    p.add_argument("--tickers", default=None,
                   help="Comma-separated (default: universe file)")
    p.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    return p.parse_args()


def main():
    args = _parse_args()
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = json.loads(Path(args.universe).read_text())["tickers"]

    baseline_root = Path(args.baseline)
    gated_root = Path(args.gated)
    report_root = Path(args.report_root)
    report_root.mkdir(parents=True, exist_ok=True)

    logger.info("Baseline journals: %s", baseline_root)
    logger.info("Gated journals:    %s", gated_root)
    logger.info("Tickers:           %s", tickers)

    results: list[dict] = []
    for t in tickers:
        base_stats = _analyze_arm("baseline", baseline_root, t)
        gated_stats = _analyze_arm("gated", gated_root, t)
        base_records = _load_ticker_records(baseline_root, t)
        gated_records = _load_ticker_records(gated_root, t)
        recall = _true_trade_recall(base_records, gated_records)
        results.append({
            "ticker": t,
            "baseline": base_stats.to_dict(),
            "gated": gated_stats.to_dict(),
            "recall": recall,
        })
        logger.info("  %s  baseline tokens=%d  gated tokens=%d  recall=%s",
                    t, base_stats.tokens_total, gated_stats.tokens_total,
                    f"{recall['recall_vs_baseline']:.1%}" if recall["recall_vs_baseline"] is not None else "n/a")

    md = _render_markdown(results)
    (report_root / "summary.md").write_text(md, encoding="utf-8")
    (report_root / "summary.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8")
    _write_per_decision_csv(report_root / "per_decision.csv",
                            baseline_root, gated_root, tickers)

    logger.info("Wrote %s", report_root / "summary.md")
    logger.info("Wrote %s", report_root / "summary.json")
    logger.info("Wrote %s", report_root / "per_decision.csv")


if __name__ == "__main__":
    main()
