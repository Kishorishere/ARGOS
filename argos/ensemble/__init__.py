"""Ensemble aggregator (P1 + P2).

Unifies all signal sources — indicator net_bias, Kronos direction,
multi-timeframe confluence, LLM bull/bear/judge, sector RS, macro
regime, market breadth, fundamentals F-score — into ONE probabilistic
signal via Bayesian log-odds combination.

Each source contributes an unweighted log-odds term (source-specific
sign + magnitude). Per-source coefficients are pluggable so the
`update_coefficients_from_ledger()` job can retune them from realized
outcomes (P12 already stores the ledger).

Public API:
  - `extract_sources(state, horizon)` → list[SignalContribution]
  - `combine(contributions)` → EnsembleSignal
  - `combined_probability(state, horizon)` → convenience wrapper
"""

from argos.ensemble.sources import (
    SignalContribution,
    EnsembleSignal,
    extract_sources,
    combine,
    combined_probability,
)

__all__ = [
    "SignalContribution",
    "EnsembleSignal",
    "extract_sources",
    "combine",
    "combined_probability",
]
