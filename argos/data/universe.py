"""Universe-level helpers backed by the cached NEPSE OHLCV pickles.

These are things that only make sense when compared *across* the universe
(cross-sectional momentum, rank percentile, etc.). One-ticker signals
belong in `argos/indicators/`.

Everything here reads only from `data/cache/nepse_ohlcv/*.pkl` — the same
cache `argos/data/nepse_ohlcv.py::load_nepse_ohlcv` writes. No network
calls; no lookahead: the caller passes an `as_of_date` and we truncate.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "nepse_ohlcv"


def cached_symbols() -> list[str]:
    """Sorted list of every ticker that has a cached OHLCV pickle."""
    return sorted(p.stem for p in CACHE_DIR.glob("*.pkl"))


@lru_cache(maxsize=64)
def _load_frame(symbol: str) -> pd.DataFrame:
    p = CACHE_DIR / f"{symbol}.pkl"
    if not p.exists():
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.read_pickle(p).copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _xsec_score(symbol: str, as_of: pd.Timestamp, min_volume: float = 10_000) -> float | None:
    """Terminal's xsec definition: 6-month return minus last month.

    Returns None if the symbol doesn't have >=130 bars up to `as_of` or fails
    the volume filter (mean 20-bar volume must be >= min_volume). The default
    threshold is NEPSE-calibrated (10k), not the terminal's 100k US-market
    default — our cached watchlist is thinner than a US universe.
    """
    df = _load_frame(symbol)
    if df.empty:
        return None
    df = df[df["date"] <= as_of]
    if len(df) < 130:
        return None
    tail = df.tail(130)
    if tail["volume"].tail(20).mean() < min_volume:
        return None
    close = tail["close"].values
    if close[-126] <= 0 or close[-21] <= 0:
        return None
    ret_6m = close[-1] / close[-126] - 1
    ret_1m = close[-1] / close[-21] - 1
    return ret_6m - ret_1m


@lru_cache(maxsize=512)
def _universe_scores_cached(as_of_iso: str) -> tuple[tuple[str, float], ...]:
    """(symbol, xsec_score) sorted desc. Cached per as_of_date."""
    as_of = pd.to_datetime(as_of_iso)
    out: list[tuple[str, float]] = []
    for sym in cached_symbols():
        s = _xsec_score(sym, as_of)
        if s is None:
            continue
        out.append((sym, s))
    out.sort(key=lambda x: x[1], reverse=True)
    return tuple(out)


def xsec_momentum_strength(symbol: str, as_of_date: str) -> tuple[float, str] | None:
    """Return a signed strength for `symbol`'s cross-sectional rank on `as_of_date`.

    Uses a universe z-score of the (6m − 1m) return metric, clipped to
    [−2, +2]. For our 11-ticker cache decile buckets are too coarse — a
    z-score gives smooth resolution and scales naturally when the
    universe grows.

    Returns (strength, note) or None if the universe is too small (<5 syms)
    or this symbol isn't in it.
    """
    import statistics
    scores = _universe_scores_cached(as_of_date)
    if len(scores) < 5:
        return None
    values = [v for _, v in scores]
    sym_to_val = {sym: v for sym, v in scores}
    if symbol not in sym_to_val:
        return None
    if len(values) < 2:
        return None
    mu = statistics.mean(values)
    sigma = statistics.pstdev(values)
    if sigma == 0:
        return None
    z = (sym_to_val[symbol] - mu) / sigma
    z_clip = max(-2.0, min(2.0, z))
    rank = sorted(values, reverse=True).index(sym_to_val[symbol]) + 1
    return z_clip, f"xsec z={z_clip:+.2f} (rank {rank}/{len(scores)})"


def clear_cache() -> None:
    """Reset the lru_caches — call after ingesting fresh data mid-process."""
    _load_frame.cache_clear()
    _universe_scores_cached.cache_clear()
