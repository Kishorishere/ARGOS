"""Non-LLM baseline comparators for the paper's §6 realized-return table.

Every Sharpe/accuracy claim in the paper needs an answer to "vs what?".
This script produces three comparators from price data only, plus
bootstrap confidence intervals on the Argos--vs--baseline Sharpe deltas.

Comparators:
  1. buy_and_hold   -- enter long on day 1, exit on last day. The null
                       hypothesis every finance paper must beat.
  2. ma_cross_20_50 -- classic 20d/50d moving-average cross; long when
                       MA20 > MA50, flat otherwise. Zero LLM, zero
                       judgment, published a thousand times.
  3. random         -- sample BUY/HOLD at the EMPIRICAL rate observed in
                       Argos's baseline decisions per ticker, 1000 draws,
                       report mean Sharpe + 95% band. If Argos is not
                       statistically distinguishable from a random policy
                       at the same trade frequency, the paper admits it.

Bootstrap CIs:
  For each (ticker, strategy) we compute Sharpe on the per-period
  return series; CI comes from resampling the per-period returns with
  replacement 1000 times.

Inputs:
  Argos per-decision journals (for action frequencies and realized returns)
  yfinance daily OHLC for the same tickers and window

Outputs:
  reports/baselines/summary.json
  reports/baselines/summary.md       headline comparison table
  reports/baselines/equity.png       stacked equity curves per ticker

Usage:
  python scripts/baselines.py \
      --universe data/universe/nasdaq_focus.json \
      --start 2024-01-02 --end 2024-12-31 \
      --journal-root logs/decisions/nasdaq_focus \
      --out reports/baselines
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env", override=True)

from scripts.eval_nasdaq import (  # noqa: E402
    _load_ticker_records,
    _paper_nav,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("baselines")

TRADING_DAYS_PER_YEAR = 252
PERIODS_PER_YEAR_WEEKLY = 52
N_BOOTSTRAP = 1000


# ── Price loader ──────────────────────────────────────────────────────────


def _load_closes(ticker: str, start: str, end: str) -> list[tuple[str, float]]:
    """Return sorted [(YYYY-MM-DD, close), ...] over [start, end] via yfinance."""
    import yfinance as yf
    import pandas as pd

    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=5)
    df = yf.download(ticker, start=start, end=end_dt.strftime("%Y-%m-%d"),
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = []
    for d, c in zip(df.index, df["Close"]):
        s = d.strftime("%Y-%m-%d")
        if start <= s <= end:
            out.append((s, float(c)))
    return out


# ── Strategy implementations (each returns per-period returns + equity curve) ─


@dataclass
class StrategyResult:
    name: str
    ticker: str
    periods: list[str] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)  # starts at 1.0
    period_returns: list[float] = field(default_factory=list)

    @property
    def total_return(self) -> Optional[float]:
        if not self.equity:
            return None
        return self.equity[-1] / self.equity[0] - 1.0

    def sharpe(self, periods_per_year: int = PERIODS_PER_YEAR_WEEKLY
               ) -> Optional[float]:
        rs = self.period_returns
        if len(rs) < 5:
            return None
        mean = statistics.mean(rs)
        std = statistics.pstdev(rs)
        if std == 0:
            return None
        return (mean / std) * math.sqrt(periods_per_year)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "ticker": self.ticker,
            "n_periods": len(self.period_returns),
            "total_return_pct": None if self.total_return is None
                else round(self.total_return * 100, 3),
            "sharpe": None if self.sharpe() is None else round(self.sharpe(), 3),
            "equity_curve": self.equity,
            "period_returns": self.period_returns,
        }


def _buy_and_hold(ticker: str, closes: list[tuple[str, float]]
                  ) -> StrategyResult:
    """Long on day one, hold to end. Daily returns (not weekly — we want
    the daily Sharpe to be directly comparable to published figures)."""
    r = StrategyResult(name="buy_and_hold", ticker=ticker)
    if len(closes) < 2:
        return r
    for i, (d, c) in enumerate(closes):
        r.periods.append(d)
        r.equity.append(c / closes[0][1])
        if i > 0:
            prev = closes[i - 1][1]
            if prev > 0:
                r.period_returns.append(c / prev - 1.0)
    return r


def _ma_cross(ticker: str, closes: list[tuple[str, float]],
              short: int = 20, long: int = 50) -> StrategyResult:
    """Long when MA_short > MA_long, flat otherwise. Daily returns."""
    r = StrategyResult(name=f"ma_cross_{short}_{long}", ticker=ticker)
    if len(closes) < long + 2:
        return r
    prices = [c for _, c in closes]
    pos = 0  # 0 = flat, 1 = long
    cash = 1.0
    shares = 0.0
    for i, (d, c) in enumerate(closes):
        if i >= long:
            ma_s = sum(prices[i - short + 1:i + 1]) / short
            ma_l = sum(prices[i - long + 1:i + 1]) / long
            target = 1 if ma_s > ma_l else 0
            if target != pos:
                if target == 1:
                    shares = cash / c; cash = 0.0
                else:
                    cash = shares * c; shares = 0.0
                pos = target
        equity = cash + shares * c
        r.periods.append(d)
        r.equity.append(equity)
        if i > 0 and r.equity[-2] > 0:
            r.period_returns.append(r.equity[-1] / r.equity[-2] - 1.0)
    return r


def _random_policy(ticker: str, closes: list[tuple[str, float]],
                   buy_prob: float, sell_prob: float, seed: int
                   ) -> StrategyResult:
    """BUY/SELL/HOLD daily at (buy_prob, sell_prob) — matched to the
    empirical frequency observed in Argos's baseline decisions.

    Sell-at-signal, stays long till next sell. We don't model short selling
    (matches simulate.py).
    """
    rng = random.Random(seed)
    r = StrategyResult(name="random", ticker=ticker)
    if len(closes) < 2:
        return r
    cash = 1.0
    shares = 0.0
    for i, (d, c) in enumerate(closes):
        u = rng.random()
        if u < buy_prob and shares == 0 and c > 0:
            shares = cash / c; cash = 0.0
        elif u < buy_prob + sell_prob and shares > 0:
            cash = shares * c; shares = 0.0
        equity = cash + shares * c
        r.periods.append(d)
        r.equity.append(equity)
        if i > 0 and r.equity[-2] > 0:
            r.period_returns.append(r.equity[-1] / r.equity[-2] - 1.0)
    return r


def _random_policy_bootstrap(ticker: str, closes: list[tuple[str, float]],
                             buy_prob: float, sell_prob: float,
                             n_draws: int = N_BOOTSTRAP) -> dict:
    """Run _random_policy `n_draws` times with different seeds; return the
    mean Sharpe and a 95% band from the empirical draw distribution."""
    sharpes: list[float] = []
    returns: list[float] = []
    for seed in range(n_draws):
        r = _random_policy(ticker, closes, buy_prob, sell_prob, seed)
        s = r.sharpe(periods_per_year=TRADING_DAYS_PER_YEAR)
        if s is not None:
            sharpes.append(s)
        tr = r.total_return
        if tr is not None:
            returns.append(tr)
    if not sharpes:
        return {"n": 0, "mean_sharpe": None,
                "p025_sharpe": None, "p975_sharpe": None,
                "mean_return_pct": None}
    sharpes.sort(); returns.sort()
    def _pct(xs, p): return xs[max(0, min(len(xs) - 1, int(p * len(xs))))]
    return {
        "n": len(sharpes),
        "mean_sharpe": round(statistics.mean(sharpes), 3),
        "p025_sharpe": round(_pct(sharpes, 0.025), 3),
        "p975_sharpe": round(_pct(sharpes, 0.975), 3),
        "mean_return_pct": round(statistics.mean(returns) * 100, 3)
                if returns else None,
    }


# ── Bootstrap CI on Argos ─────────────────────────────────────────────────


def _bootstrap_sharpe(period_returns: list[float],
                      periods_per_year: int = PERIODS_PER_YEAR_WEEKLY,
                      n_draws: int = N_BOOTSTRAP, seed: int = 0
                      ) -> dict:
    """Resample with replacement from `period_returns`; recompute Sharpe
    on each bootstrap sample; return mean + 95% CI."""
    if len(period_returns) < 5:
        return {"n": len(period_returns), "mean_sharpe": None,
                "p025": None, "p975": None}
    rng = random.Random(seed)
    n = len(period_returns)
    sharpes = []
    for _ in range(n_draws):
        sample = [period_returns[rng.randrange(n)] for _ in range(n)]
        mean = statistics.mean(sample)
        std = statistics.pstdev(sample)
        if std == 0:
            continue
        sharpes.append((mean / std) * math.sqrt(periods_per_year))
    if not sharpes:
        return {"n": n, "mean_sharpe": None, "p025": None, "p975": None}
    sharpes.sort()
    def _pct(xs, p): return xs[max(0, min(len(xs) - 1, int(p * len(xs))))]
    return {
        "n": n,
        "mean_sharpe": round(statistics.mean(sharpes), 3),
        "p025": round(_pct(sharpes, 0.025), 3),
        "p975": round(_pct(sharpes, 0.975), 3),
    }


# ── Argos policy summary (from existing eval) ─────────────────────────────


def _argos_summary(journal_root: Path, ticker: str) -> dict:
    """Pull Argos's per-period return series + decision frequencies from
    the existing paper-simulator replay."""
    nav, period_returns, sim_stats = _paper_nav(journal_root, ticker)
    records = _load_ticker_records(journal_root, ticker)
    n = sum(1 for r in records if not r.get("error"))
    n_buy = sum(1 for r in records if ((r.get("final_decision") or {}).get("action") or "").lower() == "buy")
    n_sell = sum(1 for r in records if ((r.get("final_decision") or {}).get("action") or "").lower() == "sell")
    buy_prob = n_buy / n if n else 0.0
    sell_prob = n_sell / n if n else 0.0
    return {
        "n_decisions": n,
        "n_buy": n_buy, "n_sell": n_sell,
        "buy_prob": round(buy_prob, 4),
        "sell_prob": round(sell_prob, 4),
        "sim_stats": sim_stats,
        "period_returns": period_returns,
        "nav": nav,
    }


# ── Rendering ─────────────────────────────────────────────────────────────


def _plot_equity(all_results: dict[str, dict], out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed; skipping plot")
        return
    tickers = list(all_results.keys())
    n = len(tickers)
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 4), dpi=110, sharey=False)
    if n == 1:
        axes = [axes]
    for ax, t in zip(axes, tickers):
        r = all_results[t]
        bh = r["buy_and_hold"]
        ma = r["ma_cross"]
        ax.plot(range(len(bh["equity_curve"])), bh["equity_curve"],
                label="buy_and_hold", linewidth=1.2)
        ax.plot(range(len(ma["equity_curve"])), ma["equity_curve"],
                label="ma_cross_20_50", linewidth=1.2)
        # Argos equity curve from its own sim
        argos_nav = r["argos"]["nav"]
        if argos_nav and argos_nav[0] > 0:
            ax.plot(range(len(argos_nav)), [v / argos_nav[0] for v in argos_nav],
                    label="argos (baseline)", linewidth=1.5, linestyle="--")
        ax.axhline(1.0, color="gray", linestyle=":", linewidth=0.6)
        ax.set_title(t)
        ax.set_xlabel("trading day index")
        if ax is axes[0]:
            ax.set_ylabel("equity (base=1.0)")
        ax.legend(loc="best", fontsize=9)
        ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    logger.info("Wrote %s", out_path)


def _render_markdown(all_results: dict[str, dict]) -> str:
    L = ["# Non-LLM baselines vs Argos", ""]
    L.append("Buy-and-hold is the null hypothesis every claim in §6 must beat. "
             "MA-cross is a published zero-cost comparator. Random is sampled at "
             "the EMPIRICAL (buy, sell) rate observed in Argos's own baseline "
             "journals per ticker, 1000 draws. Sharpe CIs are 95% bootstrap "
             "(1000 resamples with replacement of the per-period return series).")
    L.append("")
    L.append("## Headline table")
    L.append("")
    L.append("| Ticker | Strategy | Return% | Sharpe | 95% CI | n periods |")
    L.append("|---|---|---:|---:|---:|---:|")
    for t, r in all_results.items():
        for key, label in (("buy_and_hold", "buy_and_hold"),
                           ("ma_cross", "ma_cross_20_50")):
            s = r[key]
            L.append("| " + " | ".join([
                t, label,
                "—" if s["total_return_pct"] is None else f"{s['total_return_pct']:+.2f}%",
                "—" if s["sharpe"] is None else f"{s['sharpe']:+.2f}",
                "—",
                str(s["n_periods"]),
            ]) + " |")
        # Random (bootstrap over 1000 draws)
        rb = r["random"]
        L.append("| " + " | ".join([
            t, "random (emp.rate)",
            "—" if rb["mean_return_pct"] is None else f"{rb['mean_return_pct']:+.2f}%",
            "—" if rb["mean_sharpe"] is None else f"{rb['mean_sharpe']:+.2f}",
            "—" if rb["p025_sharpe"] is None else f"[{rb['p025_sharpe']:+.2f}, {rb['p975_sharpe']:+.2f}]",
            str(rb["n"]),
        ]) + " |")
        # Argos itself with bootstrap CI
        ac = r["argos_ci"]
        argos_ret = r["argos"]["sim_stats"].get("return_pct") if r["argos"]["sim_stats"] else None
        L.append("| " + " | ".join([
            t, "**argos (ours)**",
            "—" if argos_ret is None else f"{argos_ret:+.2f}%",
            "—" if ac["mean_sharpe"] is None else f"**{ac['mean_sharpe']:+.2f}**",
            "—" if ac["p025"] is None else f"[{ac['p025']:+.2f}, {ac['p975']:+.2f}]",
            str(ac["n"]),
        ]) + " |")
        L.append("| | | | | | |")
    L.append("")
    L.append("Reading this table: Argos must beat buy-and-hold AND its 95% CI "
             "must sit above the random policy's CI to claim meaningful alpha. "
             "If the random-policy CI contains Argos's Sharpe, the paper reports "
             "no distinguishable alpha at this sample size.")
    L.append("")
    return "\n".join(L)


# ── CLI ───────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Non-LLM baseline comparators")
    p.add_argument("--universe",
                   default=str(REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"))
    p.add_argument("--tickers", default=None)
    p.add_argument("--journal-root",
                   default=str(REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"))
    p.add_argument("--start", default="2024-01-02")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--out", default=str(REPO_ROOT / "reports" / "baselines"))
    return p.parse_args()


def main():
    args = _parse_args()
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = json.loads(Path(args.universe).read_text())["tickers"]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    journal_root = Path(args.journal_root)

    all_results: dict[str, dict] = {}
    for t in tickers:
        logger.info("-- %s --", t)
        closes = _load_closes(t, args.start, args.end)
        logger.info("  %d daily closes loaded", len(closes))
        if len(closes) < 50:
            logger.warning("  too few closes, skipping %s", t)
            continue

        bh = _buy_and_hold(t, closes).to_dict()
        ma = _ma_cross(t, closes).to_dict()

        argos = _argos_summary(journal_root, t)
        logger.info("  argos: n_dec=%d buy_p=%.3f sell_p=%.3f",
                    argos["n_decisions"], argos["buy_prob"], argos["sell_prob"])

        rb = _random_policy_bootstrap(t, closes, argos["buy_prob"], argos["sell_prob"])
        argos_ci = _bootstrap_sharpe(argos["period_returns"])

        all_results[t] = {
            "buy_and_hold": bh,
            "ma_cross": ma,
            "random": rb,
            "argos": {
                "nav": argos["nav"],
                "period_returns": argos["period_returns"],
                "n_decisions": argos["n_decisions"],
                "buy_prob": argos["buy_prob"],
                "sell_prob": argos["sell_prob"],
                "sim_stats": argos["sim_stats"],
            },
            "argos_ci": argos_ci,
        }
        logger.info("  buy_and_hold Sharpe=%s  ma_cross Sharpe=%s  "
                    "random mean Sharpe=%s  argos CI=%s",
                    bh["sharpe"], ma["sharpe"],
                    rb.get("mean_sharpe"), argos_ci.get("mean_sharpe"))

    # Trim equity-curve arrays from json for readability
    json_payload = {}
    for t, r in all_results.items():
        pr = dict(r)
        pr["argos"] = {k: v for k, v in r["argos"].items()
                       if k not in ("nav", "period_returns")}
        pr["buy_and_hold"] = {k: v for k, v in r["buy_and_hold"].items()
                              if k not in ("equity_curve", "period_returns")}
        pr["ma_cross"] = {k: v for k, v in r["ma_cross"].items()
                          if k not in ("equity_curve", "period_returns")}
        json_payload[t] = pr

    (out_root / "summary.json").write_text(
        json.dumps(json_payload, indent=2), encoding="utf-8")
    (out_root / "summary.md").write_text(
        _render_markdown(all_results), encoding="utf-8")
    _plot_equity(all_results, out_root / "equity.png")
    logger.info("Wrote %s/{summary.json, summary.md, equity.png}", out_root)


if __name__ == "__main__":
    main()
