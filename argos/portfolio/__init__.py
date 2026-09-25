"""Paper-portfolio state for Argos.

The Risk Manager needs a real portfolio snapshot to reason about sector
concentration, cash, and open exposure. Until this brain is wired into
nepse-quant-terminal's real paper-trading engine, that snapshot is a
JSON-file-backed stub the user can edit by hand — a "paper portfolio".
"""

from argos.portfolio.state import (
    PortfolioSnapshot,
    Position,
    load_portfolio,
    empty_portfolio,
)

__all__ = ["PortfolioSnapshot", "Position", "load_portfolio", "empty_portfolio"]
