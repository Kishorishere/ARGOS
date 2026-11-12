"""Paper-P&L replay of the decision journal.

Reads the JSONL journal produced by run_backtest, applies each date's
FinalDecision to a paper portfolio, fills at the NEXT bar's open (proxied
by the same day's close since the loader is as-of-date bounded), applies
bps slippage + commission, and prints end-of-window NAV / hit-rate /
average holding-period return.

This is deliberately simple — it exists so the machine's decisions have a
scorecard, not so it competes with the terminal's real backtester.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from argos.data.ohlcv_loader import load_ohlcv

logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    entry_date: str
    entry_price: float
    quantity: float
    action: str  # "long" or "short"
    exit_date: str | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    exit_reason: str | None = None  # "stop", "target", "trailing", "time", "signal"

    # U3 exit-plan fields (populated at entry from FinalDecision)
    stop_pct: float | None = None
    target_pct: float | None = None
    trailing_atr: float | None = None
    time_stop_bars: int | None = None
    bars_held: int = 0
    highest_since_entry: float = 0.0  # for long trailing stop
    lowest_since_entry: float = 0.0   # reserved for short trailing stop

    @property
    def return_pct(self) -> float:
        if self.exit_price is None or self.entry_price == 0:
            return 0.0
        direction = 1 if self.action == "long" else -1
        return direction * (self.exit_price / self.entry_price - 1) * 100


# ── Nepal-realistic fee model ────────────────────────────────────────────
# NEPSE retail brokerage tiers (2024): 0.40% on trades ≤ NPR 50k,
# stepping down to 0.36% > NPR 50k, 0.34% > NPR 500k. We use 0.40% as a
# conservative baseline. SEBON regulatory fee = 0.015% per side. DP charge
# ≈ NPR 25 flat per sell (ignored — noise at our sizes).
# Capital gains tax on realized short-term profit (<1yr): 7.5% for
# individuals as of 2024. Only applied to winning trades' P&L.
NEPAL_COMMISSION_PCT = 0.40   # per side
NEPAL_SEBON_PCT = 0.015       # per side
NEPAL_CGT_SHORT_TERM = 7.5    # % of realized profit


@dataclass
class Simulator:
    starting_cash: float = 100_000.0
    # US retail realistic: 1 bp half-spread / slippage per side on liquid
    # large-caps (NVDA, TSLA, ORCL, KO). Wider names should pass 2–3.
    slippage_bps: float = 1.0
    # Zero-commission brokers (Robinhood, IBKR Lite, Schwab) are the retail
    # baseline on US equities. The broker's revenue is PFOF, which we already
    # capture via slippage_bps above — don't double-count it as commission.
    commission_bps: float = 0.0
    # Fee profile: default US retail (0 commission, 1 bp slippage, 0 CGT) for
    # the NASDAQ research harness. Flip nepal_fees=True to re-enable the
    # tiered NEPSE broker fee + SEBON + short-term CGT schedule below.
    nepal_fees: bool = False
    apply_cgt: bool = False
    # Overrides (defaults match the constants above)
    commission_pct: float = NEPAL_COMMISSION_PCT
    sebon_pct: float = NEPAL_SEBON_PCT
    cgt_pct: float = NEPAL_CGT_SHORT_TERM

    cash: float = field(init=False)
    open_trade: PaperTrade | None = field(default=None, init=False)
    trades: list[PaperTrade] = field(default_factory=list, init=False)
    fees_paid: float = field(default=0.0, init=False)
    cgt_paid: float = field(default=0.0, init=False)
    # Dense mark-to-market series: one (date, equity) per decision step.
    nav_curve: list[dict] = field(default_factory=list, init=False)

    def __post_init__(self):
        self.cash = self.starting_cash

    def mark_to_market(self, date: str, price: float) -> float:
        """Record NAV at `date` using `price` to value any open position."""
        equity = self.cash
        if self.open_trade is not None:
            equity += self.open_trade.quantity * price
        self.nav_curve.append({"date": date, "equity": round(equity, 4),
                               "price": round(price, 4),
                               "has_position": self.open_trade is not None})
        return equity

    def _apply_slippage(self, price: float, side: str) -> float:
        adj = self.slippage_bps / 10_000
        return price * (1 + adj) if side == "buy" else price * (1 - adj)

    def _side_fee(self, notional: float) -> float:
        if self.nepal_fees:
            return notional * (self.commission_pct + self.sebon_pct) / 100
        return notional * self.commission_bps / 10_000

    def _close_position(self, date: str, price: float, reason: str) -> None:
        if self.open_trade is None:
            return
        fill = self._apply_slippage(price, "sell")
        notional = self.open_trade.quantity * fill
        fee = self._side_fee(notional)
        gross_pnl = (fill - self.open_trade.entry_price) * self.open_trade.quantity
        cgt = (gross_pnl * self.cgt_pct / 100) if (self.apply_cgt and gross_pnl > 0) else 0.0
        self.cash += notional - fee - cgt
        self.fees_paid += fee
        self.cgt_paid += cgt
        self.open_trade.exit_date = date
        self.open_trade.exit_price = fill
        self.open_trade.pnl = gross_pnl - cgt
        self.open_trade.exit_reason = reason
        self.trades.append(self.open_trade)
        self.open_trade = None

    def check_exits(self, date: str, bar_high: float, bar_low: float,
                    bar_close: float, bar_atr: float | None = None) -> str | None:
        """Called BEFORE step() every bar to see if any exit rule fires on an
        OPEN long position. Returns the reason ("stop"/"target"/"trailing"/"time")
        if the position was closed, else None.

        Order of priority: hard stop → target → trailing → time. Uses bar high/low
        to determine hit — same-bar conservatism (if stop and target both intra-bar,
        assume stop hits first).
        """
        t = self.open_trade
        if t is None or t.action != "long":
            return None

        # Update trailing basis
        if bar_high > t.highest_since_entry:
            t.highest_since_entry = bar_high
        t.bars_held += 1

        # 1) Hard stop
        if t.stop_pct is not None:
            stop_price = t.entry_price * (1 - t.stop_pct)
            if bar_low <= stop_price:
                self._close_position(date, stop_price, "stop")
                return "stop"

        # 2) Target
        if t.target_pct is not None:
            target_price = t.entry_price * (1 + t.target_pct)
            if bar_high >= target_price:
                self._close_position(date, target_price, "target")
                return "target"

        # 3) Trailing (Chandelier: activate once we're 1×ATR in profit)
        if t.trailing_atr is not None and bar_atr is not None:
            if t.highest_since_entry >= t.entry_price + bar_atr:
                trail_price = t.highest_since_entry - t.trailing_atr * bar_atr
                if bar_low <= trail_price:
                    self._close_position(date, trail_price, "trailing")
                    return "trailing"

        # 4) Time stop
        if t.time_stop_bars is not None and t.bars_held >= t.time_stop_bars:
            self._close_position(date, bar_close, "time")
            return "time"

        return None

    def step(self, date: str, action: str, position_size: float, close_price: float,
             confidence: float = 1.0,
             stop_pct: float | None = None, target_pct: float | None = None,
             trailing_atr: float | None = None, time_stop_bars: int | None = None,
             atr: float | None = None) -> None:
        # U3: check exits FIRST on any open position, then process new signal.
        # (Signal-driven exit on 'sell' still supported below.)
        if action == "buy" and self.open_trade is None and position_size > 0:
            notional = self.cash * position_size
            fill = self._apply_slippage(close_price, "buy")
            qty = notional / fill
            fee = self._side_fee(notional)
            self.cash -= notional + fee
            self.fees_paid += fee
            self.open_trade = PaperTrade(
                entry_date=date, entry_price=fill, quantity=qty, action="long",
                stop_pct=stop_pct, target_pct=target_pct,
                trailing_atr=trailing_atr, time_stop_bars=time_stop_bars,
                highest_since_entry=fill,
            )

        elif action == "sell" and self.open_trade is not None:
            self._close_position(date, close_price, "signal")

        # "hold" and any other action: no-op (short-selling not modelled)

    def close_open_trade(self, date: str, last_price: float) -> None:
        if self.open_trade is None:
            return
        self._close_position(date, last_price, "end_of_window")

    def report(self, last_price: float | None = None) -> dict:
        wins = sum(1 for t in self.trades if t.pnl > 0)
        losses = sum(1 for t in self.trades if t.pnl < 0)
        avg_ret = (sum(t.return_pct for t in self.trades) / len(self.trades)) if self.trades else 0.0
        equity = self.cash
        if self.open_trade is not None and last_price is not None:
            equity += self.open_trade.quantity * last_price
        # Round-trip breakeven: the move needed to break even on one entry +
        # one exit after fees AND slippage. Nepal mode uses the tiered broker
        # schedule; flat_bps mode counts commission + slippage per side.
        round_trip_pct = 2 * (self.commission_pct + self.sebon_pct) if self.nepal_fees \
            else 2 * (self.commission_bps + self.slippage_bps) / 100
        # U3 exit-reason breakdown for post-run analysis
        by_reason: dict[str, int] = {}
        for t in self.trades:
            r = t.exit_reason or "signal"
            by_reason[r] = by_reason.get(r, 0) + 1
        return {
            "trades": len(self.trades),
            "wins": wins,
            "losses": losses,
            "hit_rate_pct": round(100 * wins / max(wins + losses, 1), 2),
            "avg_holding_return_pct": round(avg_ret, 3),
            "starting_cash": round(self.starting_cash, 2),
            "final_cash": round(self.cash, 2),
            "final_equity_incl_open": round(equity, 2),
            "return_pct": round(100 * (equity / self.starting_cash - 1), 3),
            "total_fees_paid": round(self.fees_paid, 2),
            "total_cgt_paid": round(self.cgt_paid, 2),
            "breakeven_per_trade_pct": round(round_trip_pct, 3),
            "fee_mode": "nepal" if self.nepal_fees else "flat_bps",
            "exits_by_reason": by_reason,
            "nav_curve": self.nav_curve,
        }


def _iter_journal(journal_dir: Path, ticker: str) -> list[dict]:
    records: list[dict] = []
    for p in sorted(journal_dir.glob(f"{ticker}_*.jsonl")):
        if "_combined" in p.stem:
            continue
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def replay(journal_dir: Path, ticker: str, starting_cash: float = 100_000.0,
           nepal_fees: bool = False) -> dict:
    """Replay all journal decisions for `ticker` and return the P&L stats.

    `starting_cash` defaults to $100k for NEPSE legacy; the NASDAQ research
    harness passes 1000.0 so each ticker gets a $1k book and the equity curve
    is directly comparable across names.

    The simulator mark-to-markets on EVERY decision date (not just entry /
    exit) so the returned `nav_curve` is dense enough for Sharpe/Sortino.
    """
    records = _iter_journal(journal_dir, ticker)
    if not records:
        logger.warning("No journal records for ticker=%s in %s", ticker, journal_dir)
        return {}

    latest_by_date: dict[str, dict] = {}
    for r in records:
        d = r.get("date")
        if d:
            latest_by_date[d] = r
    ordered = [latest_by_date[d] for d in sorted(latest_by_date)]

    sim = Simulator(starting_cash=starting_cash, nepal_fees=nepal_fees)
    last_price = None
    for r in ordered:
        decision = r.get("final_decision") or {}
        action = decision.get("action", "hold")
        size = float(decision.get("position_size", 0.0))
        confidence = float(decision.get("confidence", 0.0))
        last_close = (r.get("ohlcv_summary") or {}).get("last_close")
        if last_close is None:
            continue
        last_price = float(last_close)
        sim.step(r["date"], action, size, last_price, confidence)
        # Dense NAV: record equity after processing this decision — gives us
        # one mark per decision date even when position is held unchanged.
        sim.mark_to_market(r["date"], last_price)

    sim.close_open_trade(ordered[-1]["date"], last_price or 0.0)
    if last_price is not None:
        sim.mark_to_market(ordered[-1]["date"] + "_close", last_price)
    stats = sim.report(last_price)
    logger.info("Paper P&L for %s (start=$%.0f): return=%.2f%% trades=%d",
                ticker, starting_cash, stats["return_pct"], stats["trades"])
    return stats
