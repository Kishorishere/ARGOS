"""Daily report — every symbol scored today with all 3 horizons + realized next-day."""

from __future__ import annotations

from datetime import date

from argos.reports._common import load_journal_records, score_horizon


def daily_report(as_of: str | None = None, price_provider=None) -> dict:
    """Return the daily summary as a dict; caller can pretty-print."""
    if price_provider is None:
        from argos.data.nepse_ohlcv import load_nepse_ohlcv
        price_provider = load_nepse_ohlcv
    if as_of is None:
        as_of = date.today().isoformat()

    records = load_journal_records(since=as_of, until=as_of)
    high_conv: list[dict] = []
    for r in records:
        mhf = r.get("multi_horizon_forecast") or {}
        if not mhf.get("horizons"):
            continue
        for h_name, hz in mhf["horizons"].items():
            if (hz.get("confidence", 0) or 0) > 0.65 and \
               ((hz.get("probability_up", 0.5) > 0.70) or
                (hz.get("probability_up", 0.5) < 0.30)):
                high_conv.append({
                    "symbol": r["ticker"], "horizon": h_name,
                    "direction": hz.get("direction"),
                    "prob_up": hz.get("probability_up"),
                    "expected": hz.get("expected_return_pct"),
                    "conf": hz.get("confidence"),
                    "drivers": hz.get("key_drivers", []),
                })
    # Rolling 90-day hit rate scoring for context
    hit_rates: dict[str, dict] = {}
    from datetime import datetime, timedelta
    since_90 = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    hist = load_journal_records(since=since_90, until=as_of)
    for h_key, h_days in (("1d", 1), ("1w", 5), ("1m", 21)):
        hit_rates[h_key] = score_horizon(hist, h_days, h_key, price_provider)

    return {
        "as_of": as_of,
        "n_records_today": len(records),
        "high_conviction_picks": high_conv,
        "rolling_90d_accuracy": hit_rates,
    }
