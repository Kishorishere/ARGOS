"""Market-selector helpers.

Reads the MARKET env var (default: nasdaq) to gate market-specific data
feeds. Set MARKET=nepse to re-enable the NEPSE-only floorsheet, broker,
sebon, sector, and Nepali-sentiment feeds.

Keeping this in one module (instead of scattering `os.environ.get` calls
throughout `ensemble/sources.py` and the nodes) means the branch is
auditable and easy to test.
"""

from __future__ import annotations

import os
from typing import Literal

Market = Literal["nasdaq", "nepse"]

_VALID: tuple[Market, ...] = ("nasdaq", "nepse")


def current_market() -> Market:
    m = os.environ.get("MARKET", "nasdaq").strip().lower()
    if m not in _VALID:
        # Fail loud — silently defaulting to nasdaq on typo is worse than raising.
        raise ValueError(f"MARKET={m!r} is not one of {_VALID}. Fix the .env.")
    return m  # type: ignore[return-value]


def is_nasdaq() -> bool:
    return current_market() == "nasdaq"


def is_nepse() -> bool:
    return current_market() == "nepse"


def market_label() -> str:
    """Human-facing label used in report titles / dashboard headers."""
    return {"nasdaq": "NASDAQ (US equities)", "nepse": "NEPSE (Nepal)"}[current_market()]
