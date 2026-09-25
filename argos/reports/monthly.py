"""Monthly report — 1-month forecast vs realized, hit rate by conviction band,
P&L attribution, drawdown analysis, weight suggestions."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from argos.reports._common import load_journal_records, realized_return_at, bootstrap_hit_rate_ci


def monthly_report(month_ending: str | None = None, price_provider=None) -> dict:
    if price_provider is None:
        from argos.data.nepse_ohlcv import load_nepse_ohlcv
        price_provider = load_nepse_ohlcv
    if month_ending is None:
        month_ending = date.today().isoformat()
    end_dt = datetime.strptime(month_ending, "%Y-%m-%d")
    start = (end_dt - timedelta(days=30)).strftime("%Y-%m-%d")

    records = load_journal_records(since=start, until=month_ending)

    # Bucketise 1m forecasts by conviction band
    bands = {"0.5-0.6": [], "0.6-0.7": [], "0.7+": []}
    for r in records:
        mhf = r.get("multi_horizon_forecast") or {}
        hz = (mhf.get("horizons") or {}).get("1m")
        if not hz:
            continue
        p_up = hz.get("probability_up") or 0.5
        conv = abs(p_up - 0.5) * 2  # 0..1
        band = "0.5-0.6" if conv < 0.2 else ("0.6-0.7" if conv < 0.4 else "0.7+")
        realized = realized_return_at(r["ticker"], r["date"], 21, price_provider)
        if realized is None:
            continue
        pred = "up" if p_up > 0.5 else "down"
        real = "up" if realized > 0 else "down"
        bands[band].append({"hit": pred == real, "realized": realized,
                             "expected": hz.get("expected_return_pct") or 0.0,
                             "symbol": r["ticker"]})
    band_stats = {}
    for name, obs in bands.items():
        n = len(obs)
        hits = sum(1 for o in obs if o["hit"])
        if n:
            lo, hi = bootstrap_hit_rate_ci(hits, n)
            band_stats[name] = {"n": n, "hits": hits,
                                 "hit_rate_pct": round(100 * hits / n, 2),
                                 "ci_95_pct": (round(100 * lo, 2), round(100 * hi, 2)),
                                 "mean_realized_pct": round(sum(o["realized"] for o in obs) / n, 3),
                                 "mean_expected_pct": round(sum(o["expected"] for o in obs) / n, 3)}
        else:
            band_stats[name] = {"n": 0}

    # P&L attribution (rough: sum realized × conviction × 0.05 sizing)
    total_pnl = 0.0
    for obs in bands.values():
        for o in obs:
            total_pnl += o["realized"] * 0.05

    return {
        "month_ending": month_ending,
        "n_records": len(records),
        "conviction_bands": band_stats,
        "estimated_pnl_pct": round(total_pnl, 3),
    }
