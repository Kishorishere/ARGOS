"""NASDAQ evaluation harness — reads decision journals and reports the
directional-accuracy + risk-adjusted-return metrics that the fellowship
paper's "does this pipeline actually work?" section asks for.

Metrics (per ticker AND aggregate):
  • Directional accuracy at h ∈ {1, 5, 20} trading days — for the final
    decision AND for each analyst individually (fundamentals, sentiment,
    technicals, judge). HOLD is excluded from directional accuracy since
    it makes no claim on direction.
  • Sharpe (annualized, rf=0), Sortino, Calmar, max drawdown on the paper
    NAV curve produced by argos.simulate.replay().
  • Hit rate, avg-win/avg-loss, profit factor, expectancy (per trade).
  • Debate-symmetry sanity check: mean bull_conf vs mean bear_conf and
    their absolute gap. With the OpenAI-symmetric router this should be
    small — a persistent large gap would mean the debate is model-biased
    even without provider asymmetry.

Writes:
  reports/nasdaq_focus/summary.md  — human-readable table + notes
  reports/nasdaq_focus/summary.json — machine-readable for downstream plots
  reports/nasdaq_focus/curves.png   — per-ticker equity curves overlaid
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("eval_nasdaq")


DEFAULT_JOURNAL_ROOT = REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"
DEFAULT_REPORT_ROOT = REPO_ROOT / "reports" / "nasdaq_focus"
DEFAULT_UNIVERSE = REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"

TRADING_DAYS_PER_YEAR = 252
HORIZONS = (1, 5, 20)
DIRECTIONAL_ACTIONS = {"BUY", "SELL", "buy", "sell", "long", "short"}


# ── Data loading ──────────────────────────────────────────────────────────


def _iter_journal_files(root: Path, ticker: str):
    """Yield JSONL files under root/{ticker}/, sorted by filename (date)."""
    ticker_dir = root / ticker
    if not ticker_dir.exists():
        return
    for path in sorted(ticker_dir.glob(f"{ticker}_*.jsonl")):
        yield path


def _latest_record(path: Path) -> Optional[dict]:
    """Each journal file may hold multiple attempts (append-only). Return the last."""
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def _load_ticker_records(root: Path, ticker: str) -> list[dict]:
    records = []
    for p in _iter_journal_files(root, ticker):
        rec = _latest_record(p)
        if rec is not None:
            records.append(rec)
    return records


# ── Signal extraction ─────────────────────────────────────────────────────


def _direction_from_action(action: Optional[str]) -> Optional[int]:
    if not action:
        return None
    a = action.upper()
    if a in ("BUY", "LONG"):
        return +1
    if a in ("SELL", "SHORT"):
        return -1
    return None  # HOLD → no directional claim


def _direction_from_signal(sig: Optional[dict]) -> Optional[int]:
    if not isinstance(sig, dict):
        return None
    s = (sig.get("signal") or "").lower()
    if s == "bullish":
        return +1
    if s == "bearish":
        return -1
    return None


def _extract_agent_directions(record: dict) -> dict[str, Optional[int]]:
    """Pull each agent's directional claim as -1 / 0 / +1 / None.

    Journal layout:
      analyst_signals.{fundamentals,sentiment,technicals}
      debate.{bull,bear}
      judge_verdict (may be absent for U1 blind judge if it didn't run)
      final_decision.action
    """
    final = record.get("final_decision") or {}
    analyst_signals = record.get("analyst_signals") or {}
    debate = record.get("debate") or {}
    return {
        "final":        _direction_from_action(final.get("action")),
        "fundamentals": _direction_from_signal(analyst_signals.get("fundamentals")),
        "sentiment":    _direction_from_signal(analyst_signals.get("sentiment")),
        "technicals":   _direction_from_signal(analyst_signals.get("technicals")),
        "judge":        _judge_direction(record),
        "bull":         _debate_direction(debate.get("bull"), bull=True),
        "bear":         _debate_direction(debate.get("bear"), bull=False),
    }


def _judge_direction(record: dict) -> Optional[int]:
    """The blind judge names Argument A or B, which the graph maps back to
    bull/bear as `winner_side`. Count every non-tie verdict.
    """
    j = record.get("judge_verdict") or record.get("judge") or {}
    side = (j.get("winner_side") or "").lower()
    if side == "bull":
        return +1
    if side == "bear":
        return -1
    # Some journals store winner_side only on judge_verdict.winner_side;
    # the judge may also have emitted a top-level `winner` A/B without the
    # mapped side — skip those (can't recover the mapping post-hoc).
    return None


def _debate_direction(arg: Optional[dict], bull: bool) -> Optional[int]:
    """Return the debater's directional call as +1 (buy) or -1 (sell).

    Every debate on every date produces both a Bull and a Bear argument by
    construction — this function counts the CALL, not a confidence-gated
    subset. Bear's confidence sits around 0.40 by prompt characteristic
    (the minority-side advocate is expected to be less confident), and
    gating on conf >= 0.5 would silently drop every Bear call. Confidence
    is measured separately in `_confidence_gap`.
    """
    if not isinstance(arg, dict):
        return None
    pos = (arg.get("position") or "").lower()
    if pos == "buy":
        return +1
    if pos == "sell":
        return -1
    # Fallback to convention if position is missing
    return +1 if bull else -1


# ── Realized returns via yfinance ─────────────────────────────────────────


_realized_cache: dict[str, dict] = {}


def _realized_returns(ticker: str, dates: list[str]) -> dict[str, list[Optional[float]]]:
    """Return {date: [ret_h for h in HORIZONS]} using close-to-close returns.

    Fetches once per ticker over the [min..max+30d] window and slices.
    """
    if not dates:
        return {}
    if ticker in _realized_cache:
        closes = _realized_cache[ticker]
    else:
        import yfinance as yf
        import pandas as pd
        from datetime import datetime, timedelta

        start = min(dates)
        end_dt = datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=45)
        df = yf.download(ticker, start=start, end=end_dt.strftime("%Y-%m-%d"),
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        closes = {d.strftime("%Y-%m-%d"): float(c)
                  for d, c in zip(df.index, df["Close"])}
        _realized_cache[ticker] = closes

    sorted_dates = sorted(closes.keys())
    idx = {d: i for i, d in enumerate(sorted_dates)}

    out: dict[str, list[Optional[float]]] = {}
    for d in dates:
        rets: list[Optional[float]] = []
        if d not in idx:
            out[d] = [None] * len(HORIZONS)
            continue
        i0 = idx[d]
        p0 = closes[sorted_dates[i0]]
        for h in HORIZONS:
            j = i0 + h
            if j >= len(sorted_dates) or p0 == 0:
                rets.append(None)
            else:
                p1 = closes[sorted_dates[j]]
                rets.append(p1 / p0 - 1.0)
        out[d] = rets
    return out


# ── Metric computations ──────────────────────────────────────────────────


@dataclass
class DirectionalStats:
    horizon: int
    n_total: int = 0
    n_directional: int = 0  # excludes None/HOLD
    n_correct: int = 0

    @property
    def accuracy(self) -> Optional[float]:
        return (self.n_correct / self.n_directional) if self.n_directional else None

    @property
    def coverage(self) -> Optional[float]:
        return (self.n_directional / self.n_total) if self.n_total else None


def _score_directional(agent_dirs: list[Optional[int]],
                       realized_by_h: list[list[Optional[float]]]) -> dict[int, DirectionalStats]:
    """For each horizon, count how often the agent's sign matched the realized sign."""
    stats = {h: DirectionalStats(horizon=h) for h in HORIZONS}
    for direction, rets in zip(agent_dirs, realized_by_h):
        for hi, h in enumerate(HORIZONS):
            st = stats[h]
            st.n_total += 1
            r = rets[hi] if rets and hi < len(rets) else None
            if direction is None or r is None or r == 0:
                continue
            st.n_directional += 1
            if (direction > 0 and r > 0) or (direction < 0 and r < 0):
                st.n_correct += 1
    return stats


PERIODS_PER_YEAR = 52  # Weekly cadence => ~52 marks/year for annualisation.


def _sharpe_sortino(period_returns: list[float]) -> tuple[Optional[float], Optional[float]]:
    """Annualised Sharpe and Sortino from a per-period return series.

    Period here = one decision step (weekly). Annualisation factor = sqrt(52).
    Risk-free rate assumed zero (research-paper default; cash yields are a
    separate rebalancing concern).
    """
    if len(period_returns) < 5:
        return (None, None)
    mean = statistics.mean(period_returns)
    std = statistics.pstdev(period_returns)
    downside = [r for r in period_returns if r < 0]
    downside_std = statistics.pstdev(downside) if len(downside) >= 2 else 0.0
    scale = math.sqrt(PERIODS_PER_YEAR)
    sharpe = (mean / std) * scale if std else None
    sortino = (mean / downside_std) * scale if downside_std else None
    return sharpe, sortino


def _max_drawdown(nav_curve: list[float]) -> Optional[float]:
    if not nav_curve:
        return None
    peak = nav_curve[0]
    max_dd = 0.0
    for v in nav_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = v / peak - 1.0
            max_dd = min(max_dd, dd)
    return max_dd


def _confidence_gap(records: list[dict]) -> dict[str, float]:
    bulls = [(((r.get("debate") or {}).get("bull") or {}).get("confidence")) for r in records]
    bears = [(((r.get("debate") or {}).get("bear") or {}).get("confidence")) for r in records]
    bulls = [b for b in bulls if isinstance(b, (int, float))]
    bears = [b for b in bears if isinstance(b, (int, float))]
    if not bulls or not bears:
        return {"n": 0, "bull_mean": 0.0, "bear_mean": 0.0, "abs_gap": 0.0}
    bm = statistics.mean(bulls)
    xm = statistics.mean(bears)
    return {"n": min(len(bulls), len(bears)),
            "bull_mean": round(bm, 3), "bear_mean": round(xm, 3),
            "abs_gap": round(abs(bm - xm), 3)}


# ── NAV via simulate ─────────────────────────────────────────────────────


STARTING_CASH_PER_TICKER = 1000.0  # Research setup: $1k book per ticker.


def _paper_nav(journal_root: Path, ticker: str) -> tuple[list[float], list[float], dict]:
    """Run the paper simulator on this ticker's journal directory.

    Returns (nav_values, period_returns, simulate_stats_dict). nav_values is
    the equity-per-decision series; period_returns is step-to-step returns.
    """
    try:
        from argos.simulate import replay
    except Exception as e:  # noqa: BLE001
        logger.warning("simulate.replay import failed: %s", e)
        return [], [], {}
    ticker_dir = journal_root / ticker
    if not ticker_dir.exists():
        return [], [], {}
    try:
        stats = replay(ticker_dir, ticker, starting_cash=STARTING_CASH_PER_TICKER)
    except Exception as e:  # noqa: BLE001
        logger.warning("simulate.replay(%s) failed: %s", ticker, e)
        return [], [], {}
    curve = stats.get("nav_curve") or []
    nav_values = [pt["equity"] for pt in curve if isinstance(pt, dict)]
    if not nav_values:
        return [], [], stats if isinstance(stats, dict) else {}
    period_returns = []
    for i in range(1, len(nav_values)):
        if nav_values[i - 1] > 0:
            period_returns.append(nav_values[i] / nav_values[i - 1] - 1.0)
    return nav_values, period_returns, stats if isinstance(stats, dict) else {}


# ── Token-usage aggregation ───────────────────────────────────────────────


def _aggregate_tokens(records: list[dict]) -> dict:
    """Sum per-agent + overall tokens across every journalled decision.

    Returns {per_agent: {agent: {input, output, total, calls}},
             totals: {input, output, total, calls, decisions_covered}}
    """
    per_agent: dict[str, dict[str, int]] = {}
    total_in = total_out = total_calls = 0
    covered = 0
    for r in records:
        tu = r.get("token_usage") or {}
        per = tu.get("per_agent") or {}
        if not per:
            continue
        covered += 1
        for key, v in per.items():
            # key is "agent/label"; aggregate under bare agent name
            agent = key.split("/", 1)[0]
            dst = per_agent.setdefault(agent, {"input_tokens": 0, "output_tokens": 0,
                                                "total_tokens": 0, "calls": 0})
            dst["input_tokens"] += int(v.get("input_tokens", 0))
            dst["output_tokens"] += int(v.get("output_tokens", 0))
            dst["total_tokens"] += int(v.get("total_tokens", 0))
            dst["calls"] += int(v.get("calls", 0))
        tot = tu.get("totals") or {}
        total_in += int(tot.get("input_tokens", 0))
        total_out += int(tot.get("output_tokens", 0))
        total_calls += int(tot.get("calls", 0))
    return {
        "per_agent": per_agent,
        "totals": {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "calls": total_calls,
            "decisions_covered": covered,
            "decisions_total": len(records),
        },
    }


def _tokens_per_decision(agg: dict) -> float | None:
    t = agg.get("totals") or {}
    d = t.get("decisions_covered") or 0
    if d <= 0:
        return None
    return t["total_tokens"] / d


def _profit_per_1k_tokens(pnl_usd: float, agg: dict) -> float | None:
    t = agg.get("totals") or {}
    total = t.get("total_tokens") or 0
    if total <= 0:
        return None
    return pnl_usd / (total / 1000.0)


# ── Per-ticker aggregation ────────────────────────────────────────────────


@dataclass
class TickerReport:
    ticker: str
    n_records: int
    n_errors: int
    per_agent_directional: dict = field(default_factory=dict)
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    max_dd: Optional[float] = None
    calmar: Optional[float] = None
    pnl_usd: Optional[float] = None
    return_pct: Optional[float] = None
    confidence_gap: dict = field(default_factory=dict)
    simulate_stats: dict = field(default_factory=dict)
    token_usage: dict = field(default_factory=dict)
    tokens_per_decision: Optional[float] = None
    profit_per_1k_tokens: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "n_records": self.n_records, "n_errors": self.n_errors,
            "sharpe": self.sharpe, "sortino": self.sortino,
            "max_dd": self.max_dd, "calmar": self.calmar,
            "pnl_usd": self.pnl_usd, "return_pct": self.return_pct,
            "confidence_gap": self.confidence_gap,
            "simulate_stats": {k: v for k, v in self.simulate_stats.items()
                               if k not in ("nav_curve", "equity_curve")},
            "token_usage": self.token_usage,
            "tokens_per_decision": self.tokens_per_decision,
            "profit_per_1k_tokens": self.profit_per_1k_tokens,
            "per_agent_directional": {
                agent: {h: {"accuracy": s.accuracy, "coverage": s.coverage,
                            "n_directional": s.n_directional, "n_correct": s.n_correct}
                        for h, s in by_h.items()}
                for agent, by_h in self.per_agent_directional.items()
            },
        }


def _analyze_ticker(journal_root: Path, ticker: str) -> TickerReport:
    records = _load_ticker_records(journal_root, ticker)
    n_errors = sum(1 for r in records if r.get("error"))
    good = [r for r in records if not r.get("error") and r.get("date")]
    report = TickerReport(ticker=ticker, n_records=len(records), n_errors=n_errors)

    if not good:
        return report

    dates = [r["date"] for r in good]
    realized_map = _realized_returns(ticker, dates)
    realized_by_h_list = [realized_map.get(d, [None] * len(HORIZONS)) for d in dates]

    per_agent = {agent: [] for agent in
                 ("final", "fundamentals", "sentiment", "technicals", "judge", "bull", "bear")}
    for rec in good:
        dirs = _extract_agent_directions(rec)
        for agent, direction in dirs.items():
            per_agent[agent].append(direction)

    for agent, dirs_list in per_agent.items():
        report.per_agent_directional[agent] = _score_directional(dirs_list, realized_by_h_list)

    report.confidence_gap = _confidence_gap(good)

    nav, period_returns, sim_stats = _paper_nav(journal_root, ticker)
    if period_returns:
        report.sharpe, report.sortino = _sharpe_sortino(period_returns)
        report.max_dd = _max_drawdown(nav)
        total_return = (nav[-1] / nav[0] - 1.0) if nav and nav[0] else None
        report.return_pct = total_return * 100 if total_return is not None else None
        report.pnl_usd = (nav[-1] - nav[0]) if nav else None
        if report.max_dd and total_return is not None and report.max_dd < 0:
            # Weekly cadence: 53 marks per year => years = len(returns)/53.
            # Avoid overstating annualisation on short samples.
            n_marks = len(nav)
            years = max(n_marks / 53.0, 1e-6)
            ann_ret = (1 + total_return) ** (1 / years) - 1
            report.calmar = ann_ret / abs(report.max_dd)
    report.simulate_stats = sim_stats

    # ── Token usage + derived $-per-token metrics ──
    report.token_usage = _aggregate_tokens(good)
    report.tokens_per_decision = _tokens_per_decision(report.token_usage)
    if report.pnl_usd is not None:
        report.profit_per_1k_tokens = _profit_per_1k_tokens(report.pnl_usd, report.token_usage)
    return report


# ── Rendering ─────────────────────────────────────────────────────────────


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.2f}%"


def _fmt_ratio(x: Optional[float]) -> str:
    if x is None:
        return "—"
    return f"{x:+.2f}"


def _render_markdown(reports: list[TickerReport]) -> str:
    lines = ["# NASDAQ focus backtest — evaluation summary", ""]
    lines.append(f"Tickers: {', '.join(r.ticker for r in reports)}   Universe: `data/universe/nasdaq_focus.json`")
    lines.append("")

    lines.append("## Directional accuracy — final decision")
    lines.append("| Ticker | h=1 | h=5 | h=20 | n_records | n_errors |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in reports:
        f = r.per_agent_directional.get("final", {})
        row = [
            r.ticker,
            _fmt_pct(f[1].accuracy) if 1 in f else "—",
            _fmt_pct(f[5].accuracy) if 5 in f else "—",
            _fmt_pct(f[20].accuracy) if 20 in f else "—",
            str(r.n_records), str(r.n_errors),
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Directional accuracy — per analyst (h=5)")
    agents = ("fundamentals", "sentiment", "technicals", "judge", "bull", "bear")
    header = "| Ticker | " + " | ".join(agents) + " |"
    align = "|---|" + ":---:|" * len(agents)
    lines.extend([header, align])
    for r in reports:
        row = [r.ticker]
        for agent in agents:
            st = r.per_agent_directional.get(agent, {}).get(5)
            row.append(_fmt_pct(st.accuracy) if st else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Risk-adjusted returns")
    lines.append("| Ticker | Sharpe (ann) | Sortino | Max DD | Calmar |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in reports:
        lines.append("| " + " | ".join([
            r.ticker,
            _fmt_ratio(r.sharpe), _fmt_ratio(r.sortino),
            _fmt_pct(r.max_dd), _fmt_ratio(r.calmar),
        ]) + " |")
    lines.append("")

    lines.append("## Debate-symmetry sanity check")
    lines.append("| Ticker | n | mean bull_conf | mean bear_conf | abs gap |")
    lines.append("|---|---:|---:|---:|---:|")
    for r in reports:
        g = r.confidence_gap or {}
        lines.append("| " + " | ".join([
            r.ticker, str(g.get("n", 0)),
            f"{g.get('bull_mean', 0):.3f}", f"{g.get('bear_mean', 0):.3f}",
            f"{g.get('abs_gap', 0):.3f}",
        ]) + " |")
    lines.append("")
    lines.append("A large persistent `abs_gap` under the OpenAI-symmetric router "
                 "would mean the debate is model-biased even without provider asymmetry.")
    lines.append("")
    return "\n".join(lines)


def _plot_equity_curves(reports: list[TickerReport], output_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed — skipping equity-curve plot")
        return
    fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
    for r in reports:
        nav_rows = r.simulate_stats.get("nav_curve") or r.simulate_stats.get("equity_curve") or []
        if not nav_rows:
            continue
        # nav_curve rows are {date, equity, price, has_position} dicts.
        # Fall back to a float list for backward compatibility.
        if isinstance(nav_rows[0], dict):
            y = [row.get("equity", 0.0) for row in nav_rows]
        else:
            y = nav_rows
        ax.plot(range(len(y)), y, label=r.ticker, linewidth=1.5)
    ax.set_title("NASDAQ focus — paper NAV per ticker")
    ax.set_xlabel("Trading day")
    ax.set_ylabel("NAV")
    ax.axhline(1.0, color="gray", linewidth=0.5, linestyle="--")
    ax.legend()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    logger.info("Equity-curve plot → %s", output_path)


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Argos NASDAQ eval harness")
    p.add_argument("--journal-root", default=str(DEFAULT_JOURNAL_ROOT))
    p.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    p.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    p.add_argument("--tickers", default=None,
                   help="Comma-separated tickers (default: universe file)")
    return p.parse_args()


def main():
    args = _parse_args()
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        universe = json.loads(Path(args.universe).read_text())
        tickers = universe["tickers"]

    logger.info("Evaluating: %s", tickers)
    journal_root = Path(args.journal_root)
    report_root = Path(args.report_root)

    reports = []
    for t in tickers:
        logger.info("── %s", t)
        r = _analyze_ticker(journal_root, t)
        logger.info("    %d records, %d errors, Sharpe=%s",
                    r.n_records, r.n_errors,
                    f"{r.sharpe:.2f}" if r.sharpe is not None else "—")
        reports.append(r)

    report_root.mkdir(parents=True, exist_ok=True)
    md_path = report_root / "summary.md"
    md_path.write_text(_render_markdown(reports))
    logger.info("Wrote %s", md_path)

    json_path = report_root / "summary.json"
    json_path.write_text(json.dumps([r.to_dict() for r in reports], indent=2, default=str))
    logger.info("Wrote %s", json_path)

    _plot_equity_curves(reports, report_root / "curves.png")


if __name__ == "__main__":
    main()
