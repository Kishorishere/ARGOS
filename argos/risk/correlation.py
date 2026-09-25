"""Rolling correlation-aware sizing (P19).

If our portfolio already holds two names with 0.8 correlation to a new
candidate, the effective new position is triple exposure. This module
computes the average pairwise correlation between the new symbol and
existing holdings, and returns a `size_multiplier` in (0..1] that scales
down the new position accordingly.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _returns(close: pd.Series) -> pd.Series:
    return close.pct_change().dropna()


def correlation_adjustment(candidate_close: pd.Series,
                            existing_closes: dict[str, pd.Series],
                            window: int = 60) -> tuple[float, dict]:
    """Given candidate + dict of existing holdings' closes, return
    (size_multiplier, breakdown).

    Series index alignment: callers should pass a `pd.Series` INDEXED BY
    DATE (not RangeIndex) so cross-symbol alignment is by trading date.
    If callers pass RangeIndex, we fall back to position-based align on
    the tail(window) slice.

    size_multiplier ∈ (0.3, 1.0]:
      no existing positions        → 1.0
      avg |correlation| < 0.3      → 1.0
      avg |correlation| 0.3..0.6   → linear 1.0 → 0.6
      avg |correlation| 0.6..0.9   → linear 0.6 → 0.3
      avg |correlation| >= 0.9     → 0.3 (hard floor)

    Never returns 0 — we always allow SOME position size even if it's
    correlated to existing holdings, but strongly capped.
    """
    if not existing_closes:
        return (1.0, {"avg_abs_corr": 0.0, "n_existing": 0, "pairwise": {}})

    cand_ret = _returns(candidate_close).tail(window)
    if len(cand_ret) < 20:
        return (1.0, {"avg_abs_corr": 0.0, "n_existing": 0, "pairwise": {}})

    pairwise: dict[str, float] = {}
    abs_corrs: list[float] = []
    for sym, other in existing_closes.items():
        other_ret = _returns(other).tail(window)
        # If both series have DatetimeIndex, align by date; otherwise fall
        # back to position-based alignment by resetting index and taking
        # the last min(len(a), len(b)) rows.
        if (isinstance(cand_ret.index, pd.DatetimeIndex) and
                isinstance(other_ret.index, pd.DatetimeIndex)):
            aligned = pd.concat([cand_ret, other_ret], axis=1, join="inner").dropna()
        else:
            n = min(len(cand_ret), len(other_ret))
            if n < 20:
                continue
            a = cand_ret.iloc[-n:].reset_index(drop=True)
            b = other_ret.iloc[-n:].reset_index(drop=True)
            aligned = pd.concat([a, b], axis=1).dropna()
        if len(aligned) < 20:
            continue
        c = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if c is None or pd.isna(c):
            continue
        pairwise[sym] = round(float(c), 3)
        abs_corrs.append(abs(c))
    if not abs_corrs:
        return (1.0, {"avg_abs_corr": 0.0, "n_existing": 0, "pairwise": pairwise})

    avg = sum(abs_corrs) / len(abs_corrs)
    if avg < 0.3:
        mult = 1.0
    elif avg < 0.6:
        # 0.3 → 1.0, 0.6 → 0.6
        mult = 1.0 - (avg - 0.3) * (0.4 / 0.3)
    elif avg < 0.9:
        # 0.6 → 0.6, 0.9 → 0.3
        mult = 0.6 - (avg - 0.6) * (0.3 / 0.3)
    else:
        mult = 0.3
    return (round(mult, 3),
            {"avg_abs_corr": round(avg, 3),
             "n_existing": len(pairwise),
             "pairwise": pairwise})
