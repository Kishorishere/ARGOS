"""Multi-asset screener (U7) — rank all NEPSE symbols by cheap indicator
score, pick the top K bullish + top K bearish to feed the full agent stack.

Turns Argos from "run daily on one boring ticker" into "find the best 10
opportunities in the whole market, then think hard only about those."
"""

from argos.screener.rank import rank_universe, get_default_universe

__all__ = ["rank_universe", "get_default_universe"]
