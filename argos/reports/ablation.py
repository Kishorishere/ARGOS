"""Shared ablation harness — the paper's A/B comparison library.

Every ablation script (judge_ab, debate_rounds, kronos_ab, loo_analyst,
gate_with_triggers, …) answers the same shape of question against a pair
of journal directories: *does toggling component X preserve decision
quality while changing token cost in the expected direction?*

This module turns that shared shape into one API so each ablation CLI is
a thin ~60-LOC wrapper that only has to say:
  (a) what the two arms are called
  (b) what the headline claim of the comparison is

The research question in the paper is always the same: can we shrink the
system (gate on, drop an analyst, fewer debate rounds, stub Kronos) without
giving back directional accuracy or realized Sharpe? The library computes
all of those for both arms and returns a `PairResult` the wrapper writes to
disk.

Nothing in here touches `scripts/compare_gate_ab.py` — that script is the
paper's reference table and it stays as-is.
"""

from __future__ import annotations

import csv
import json
import logging
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Single source of truth for per-ticker metrics.
from scripts.eval_nasdaq import (  # noqa: E402
    HORIZONS,
    _aggregate_tokens,
    _direction_from_action,
    _load_ticker_records,
    _max_drawdown,
    _paper_nav,
    _realized_returns,
    _score_directional,
    _sharpe_sortino,
)

logger = logging.getLogger("argos.ablation")

__all__ = [
    "ArmStats",
    "PairResult",
    "analyze_arm",
    "compute_pair",
    "pair_recall",
    "count_decisions",
    "render_pair_markdown",
    "write_per_decision_csv",
    "write_outputs",
]


# ── Per-arm stats ───────────────────────────────────────────────────────────

@dataclass
class ArmStats:
    """Stats for one arm on one ticker. Matches compare_gate_ab.ArmStats
    shape so downstream plotters work on either output."""
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
    dir_acc: dict = field(default_factory=dict)      # {h: accuracy}
    # Arm-local extras (e.g. gate triggers, judge win rates). Freeform
    # dict so each wrapper can tuck in whatever it wants without changing
    # the schema.
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def count_decisions(records: list[dict]) -> tuple[int, int, int]:
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


def analyze_arm(arm: str, journal_root: Path, ticker: str,
                extras_fn=None) -> ArmStats:
    """Compute one arm's stats on one ticker.

    `extras_fn(records) -> dict` is optional; use it from a wrapper to
    stash arm-specific numbers (e.g. judge agreement with PM action)
    into the ArmStats.extras bag without touching the harness.
    """
    stats = ArmStats(arm=arm, ticker=ticker)
    records = _load_ticker_records(journal_root, ticker)
    good = [r for r in records if not r.get("error") and r.get("date")]
    stats.n_records = len(good)
    if not good:
        return stats

    stats.n_hold, stats.n_buy, stats.n_sell = count_decisions(good)

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

    if extras_fn is not None:
        try:
            stats.extras = dict(extras_fn(good)) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("extras_fn(%s/%s) failed: %s", arm, ticker, e)

    return stats


# ── Pairwise metrics ────────────────────────────────────────────────────────

def pair_recall(a_records: list[dict], b_records: list[dict]) -> dict:
    """Of the dates where arm A traded (BUY/SELL), what fraction did arm B
    also trade on? High recall = arm B preserves A's alpha. Low recall =
    arm B suppresses real trades.

    Reports precision too (B-trades that were also A-trades) and the
    date lists for drill-down in the per-decision CSV.
    """
    def by_date(rs):
        return {r["date"]: ((r.get("final_decision") or {}).get("action") or "").lower()
                for r in rs if r.get("date")}

    a_by = by_date(a_records)
    b_by = by_date(b_records)
    common = sorted(set(a_by) & set(b_by))
    a_trades = {d for d in common if a_by[d] in ("buy", "sell", "long", "short")}
    b_trades = {d for d in common if b_by[d] in ("buy", "sell", "long", "short")}

    tp = len(a_trades & b_trades)
    recall = tp / len(a_trades) if a_trades else None
    precision = tp / len(b_trades) if b_trades else None

    return {
        "n_common_dates": len(common),
        "a_trade_days": len(a_trades),
        "b_trade_days": len(b_trades),
        "both_trade_days": tp,
        "recall_vs_a": recall,
        "precision_vs_a": precision,
        "a_only_trade_dates": sorted(a_trades - b_trades),
        "b_only_trade_dates": sorted(b_trades - a_trades),
    }


@dataclass
class PairResult:
    """One row of the paper's ablation table."""
    ticker: str
    a: ArmStats
    b: ArmStats
    recall: dict        # pair_recall output

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "a": self.a.to_dict(),
            "b": self.b.to_dict(),
            "recall": self.recall,
        }


def compute_pair(
    tickers: list[str],
    a_root: Path,
    b_root: Path,
    a_name: str,
    b_name: str,
    a_extras_fn=None,
    b_extras_fn=None,
) -> list[PairResult]:
    out: list[PairResult] = []
    for t in tickers:
        a_stats = analyze_arm(a_name, a_root, t, extras_fn=a_extras_fn)
        b_stats = analyze_arm(b_name, b_root, t, extras_fn=b_extras_fn)
        a_records = _load_ticker_records(a_root, t)
        b_records = _load_ticker_records(b_root, t)
        recall = pair_recall(a_records, b_records)
        out.append(PairResult(ticker=t, a=a_stats, b=b_stats, recall=recall))
        logger.info("  %-6s %s=%d tok / %s=%d tok / recall %s",
                    t, a_name, a_stats.tokens_total, b_name, b_stats.tokens_total,
                    "n/a" if recall["recall_vs_a"] is None else f"{recall['recall_vs_a']:.1%}")
    return out


# ── Rendering ───────────────────────────────────────────────────────────────

def _fmt_int(v) -> str:
    return "—" if v is None else f"{int(v):,}"


def _fmt_float(v, pct: bool = False, sig: int = 2) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:+.{sig}f}%"
    return f"{v:+.{sig}f}"


def _fmt_acc(acc: dict, h: int) -> str:
    v = acc.get(h)
    return "—" if v is None else f"{v * 100:.1f}%"


def render_pair_markdown(
    title: str,
    question: str,
    arms_blurb: tuple[str, str],         # (a arm description, b arm description)
    results: list[PairResult],
    headline: str | None = None,
    extras_cols: tuple[str, ...] = (),
) -> str:
    """Produce the standard paper-style A/B markdown table.

    `arms_blurb` is a tuple of two one-line strings; the first describes
    arm A, the second arm B. `headline` is an optional extra paragraph
    right under the title (use it for the single-sentence research claim).
    `extras_cols` names fields the wrapper wants pulled out of
    ArmStats.extras and rendered as extra columns.
    """
    a_name = results[0].a.arm if results else "a"
    b_name = results[0].b.arm if results else "b"

    L = [f"# {title}", ""]
    L.append(f"**Research question:** _{question}_")
    L.append("")
    if headline:
        L.append(headline)
        L.append("")
    L.append("Arms:")
    L.append(f"- **{a_name}** — {arms_blurb[0]}")
    L.append(f"- **{b_name}** — {arms_blurb[1]}")
    L.append("")

    # Cost
    L.append("## Cost (tokens)")
    L.append(f"| Ticker | {a_name} tokens | {b_name} tokens | Δ tokens | Δ% |")
    L.append("|---|---:|---:|---:|---:|")
    for r in results:
        a, b = r.a, r.b
        delta = b.tokens_total - a.tokens_total
        pct = (delta / a.tokens_total * 100) if a.tokens_total else None
        L.append("| " + " | ".join([
            r.ticker,
            _fmt_int(a.tokens_total), _fmt_int(b.tokens_total),
            _fmt_int(delta),
            "—" if pct is None else f"{pct:+.1f}%",
        ]) + " |")
    L.append("")

    # Decision mix
    L.append("## Decision mix")
    L.append("| Ticker | Arm | n | HOLD | BUY | SELL | HOLD% |")
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for r in results:
        for arm in (r.a, r.b):
            n = arm.n_records
            hold_pct = (arm.n_hold / n * 100) if n else None
            L.append("| " + " | ".join([
                r.ticker, arm.arm, str(n),
                str(arm.n_hold), str(arm.n_buy), str(arm.n_sell),
                "—" if hold_pct is None else f"{hold_pct:.1f}%",
            ]) + " |")
    L.append("")

    # Directional accuracy
    L.append("## Directional accuracy — final decision")
    L.append("| Ticker | Arm | h=1 | h=5 | h=20 |")
    L.append("|---|---|---:|---:|---:|")
    for r in results:
        for arm in (r.a, r.b):
            L.append("| " + " | ".join([
                r.ticker, arm.arm,
                _fmt_acc(arm.dir_acc, 1),
                _fmt_acc(arm.dir_acc, 5),
                _fmt_acc(arm.dir_acc, 20),
            ]) + " |")
    L.append("")

    # Realized paper P&L
    L.append("## Realized paper P&L")
    L.append("| Ticker | Arm | Sharpe | Hit rate | PnL (USD) | Max DD |")
    L.append("|---|---|---:|---:|---:|---:|")
    for r in results:
        for arm in (r.a, r.b):
            L.append("| " + " | ".join([
                r.ticker, arm.arm,
                _fmt_float(arm.sharpe),
                "—" if arm.hit_rate_pct is None else f"{arm.hit_rate_pct:.1f}%",
                "—" if arm.pnl_usd is None else f"{arm.pnl_usd:+.2f}",
                _fmt_float(arm.max_dd, pct=True),
            ]) + " |")
    L.append("")

    # Trade-day recall headline
    L.append(f"## Trade-day recall ({a_name} → {b_name})")
    L.append(f"Of the dates where **{a_name}** issued a BUY/SELL, what fraction did **{b_name}** also issue a BUY/SELL on? "
             f"Recall → 1.0 = {b_name} preserved every trade {a_name} took; low recall = suppression.")
    L.append("")
    L.append("| Ticker | Common days | " + f"{a_name} trades | {b_name} trades | Both | Recall | Precision |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        rc = r.recall
        L.append("| " + " | ".join([
            r.ticker, str(rc["n_common_dates"]),
            str(rc["a_trade_days"]), str(rc["b_trade_days"]), str(rc["both_trade_days"]),
            _fmt_float(rc["recall_vs_a"], pct=True, sig=1),
            _fmt_float(rc["precision_vs_a"], pct=True, sig=1),
        ]) + " |")
    L.append("")

    # Arm-specific extras — one column per named extra field.
    if extras_cols:
        L.append("## Arm-specific metrics")
        hdr = "| Ticker | Arm | " + " | ".join(extras_cols) + " |"
        sep = "|---|---|" + "---:|" * len(extras_cols)
        L.append(hdr)
        L.append(sep)
        for r in results:
            for arm in (r.a, r.b):
                cells = [str((arm.extras or {}).get(c, "—")) for c in extras_cols]
                L.append("| " + " | ".join([r.ticker, arm.arm] + cells) + " |")
        L.append("")

    return "\n".join(L)


def write_per_decision_csv(
    out_path: Path,
    a_root: Path,
    b_root: Path,
    a_name: str,
    b_name: str,
    tickers: list[str],
    extra_cols: tuple[str, ...] = (),
    extra_extract=None,
) -> None:
    """Emit the paired per-decision CSV with extensible columns."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        base_header = ["ticker", "date",
                       f"{a_name}_action", f"{b_name}_action",
                       f"{a_name}_tokens", f"{b_name}_tokens"]
        w.writerow(base_header + list(extra_cols))
        for t in tickers:
            a = {r["date"]: r for r in _load_ticker_records(a_root, t) if r.get("date")}
            b = {r["date"]: r for r in _load_ticker_records(b_root, t) if r.get("date")}
            dates = sorted(set(a) | set(b))
            for d in dates:
                ra, rb = a.get(d, {}) or {}, b.get(d, {}) or {}
                row = [
                    t, d,
                    (ra.get("final_decision") or {}).get("action") or "",
                    (rb.get("final_decision") or {}).get("action") or "",
                    ((ra.get("token_usage") or {}).get("totals") or {}).get("total_tokens", ""),
                    ((rb.get("token_usage") or {}).get("totals") or {}).get("total_tokens", ""),
                ]
                if extra_extract is not None:
                    try:
                        row.extend(extra_extract(ra, rb) or [""] * len(extra_cols))
                    except Exception:  # noqa: BLE001
                        row.extend([""] * len(extra_cols))
                w.writerow(row)


# ── One-shot: compute + write all three outputs ─────────────────────────────

def write_outputs(
    report_root: Path,
    title: str,
    question: str,
    arms_blurb: tuple[str, str],
    results: list[PairResult],
    a_root: Path,
    b_root: Path,
    tickers: list[str],
    headline: str | None = None,
    extras_cols: tuple[str, ...] = (),
    csv_extra_cols: tuple[str, ...] = (),
    csv_extra_extract=None,
) -> None:
    report_root.mkdir(parents=True, exist_ok=True)
    md = render_pair_markdown(title, question, arms_blurb, results,
                              headline=headline, extras_cols=extras_cols)
    (report_root / "summary.md").write_text(md, encoding="utf-8")
    (report_root / "summary.json").write_text(
        json.dumps([r.to_dict() for r in results], indent=2, default=str),
        encoding="utf-8",
    )
    a_name = results[0].a.arm if results else "a"
    b_name = results[0].b.arm if results else "b"
    write_per_decision_csv(
        report_root / "per_decision.csv",
        a_root, b_root, a_name, b_name, tickers,
        extra_cols=csv_extra_cols, extra_extract=csv_extra_extract,
    )
    logger.info("Wrote %s", report_root / "summary.md")
    logger.info("Wrote %s", report_root / "summary.json")
    logger.info("Wrote %s", report_root / "per_decision.csv")
