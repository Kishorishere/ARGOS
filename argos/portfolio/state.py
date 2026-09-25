"""Paper portfolio snapshot — a JSON file the user (or a driver script) edits.

Schema example (data/portfolio.json):

    {
      "cash": 100000.0,
      "day_pnl": 0.0,
      "positions": [
        {"ticker": "NABIL", "sector": "Commercial Banks", "quantity": 100,
         "avg_price": 1050.5, "market_price": 1080.0},
        {"ticker": "NIFRA", "sector": "Non-Life Insurance", "quantity": 50,
         "avg_price": 920.0, "market_price": 940.0}
      ]
    }

The Risk Manager gets the snapshot serialized: current cash %, per-sector
exposure %, gross/net exposure %, and top-N holding weights, plus day-P&L.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Position(BaseModel):
    ticker: str
    sector: str = "Unknown"
    quantity: float = 0.0
    avg_price: float = 0.0
    market_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.quantity * self.market_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.market_price - self.avg_price) * self.quantity


class PortfolioSnapshot(BaseModel):
    cash: float = 100_000.0
    day_pnl: float = 0.0
    positions: list[Position] = Field(default_factory=list)

    def gross_exposure(self) -> float:
        return sum(abs(p.market_value) for p in self.positions)

    def total_equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions)

    def sector_weights(self) -> dict[str, float]:
        eq = max(self.total_equity(), 1e-9)
        weights: dict[str, float] = {}
        for p in self.positions:
            weights[p.sector] = weights.get(p.sector, 0.0) + p.market_value / eq
        return weights

    def to_risk_context(self) -> dict:
        """Compact dict the Risk Manager can reason about."""
        eq = max(self.total_equity(), 1e-9)
        return {
            "cash": round(self.cash, 2),
            "total_equity": round(self.total_equity(), 2),
            "cash_pct": round(100 * self.cash / eq, 2),
            "gross_exposure_pct": round(100 * self.gross_exposure() / eq, 2),
            "day_pnl": round(self.day_pnl, 2),
            "day_pnl_pct": round(100 * self.day_pnl / eq, 3),
            "n_positions": len(self.positions),
            "positions": [
                {
                    "ticker": p.ticker,
                    "sector": p.sector,
                    "weight_pct": round(100 * p.market_value / eq, 2),
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for p in self.positions
            ],
            "sector_weights_pct": {
                k: round(100 * v, 2) for k, v in self.sector_weights().items()
            },
        }


def empty_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot()


def load_portfolio(path: str | Path | None = None) -> PortfolioSnapshot:
    """Load a JSON portfolio file. Returns empty portfolio if missing/unreadable.

    Path resolution order:
      1. The `path` argument if given
      2. $PAPER_PORTFOLIO_PATH env var
      3. ./data/portfolio.json under CWD
    """
    resolved: Path | None
    if path is not None:
        resolved = Path(path)
    elif os.environ.get("PAPER_PORTFOLIO_PATH"):
        resolved = Path(os.environ["PAPER_PORTFOLIO_PATH"])
    else:
        resolved = Path("data") / "portfolio.json"

    if not resolved.exists():
        logger.info("Paper portfolio not found at %s — starting with empty portfolio", resolved)
        return empty_portfolio()

    try:
        raw = json.loads(resolved.read_text())
        return PortfolioSnapshot(**raw)
    except Exception as e:
        logger.warning("Failed to load paper portfolio at %s: %s — using empty", resolved, e)
        return empty_portfolio()
