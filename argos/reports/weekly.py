"""Weekly report — top-5 conviction picks, sector rotation, agent-attribution."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from argos.reports._common import load_journal_records, score_horizon


def weekly_report(week_ending: str | None = None, price_provider=None) -> dict:
    if price_provider is None:
        from argos.data.nepse_ohlcv import load_nepse_ohlcv
        price_provider = load_nepse_ohlcv
    if week_ending is None:
        week_ending = date.today().isoformat()
    end_dt = datetime.strptime(week_ending, "%Y-%m-%d")
    start = (end_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    records = load_journal_records(since=start, until=week_ending)
    signals = []
    for r in records:
        mhf = r.get("multi_horizon_forecast") or {}
        hz = (mhf.get("horizons") or {}).get("1w")
        if not hz:
            continue
        signals.append({
            "date": r["date"], "symbol": r["ticker"],
            "direction": hz.get("direction"),
            "prob_up": hz.get("probability_up"),
            "expected": hz.get("expected_return_pct"),
            "conf": hz.get("confidence"),
        })
    # Top-5 highest conviction
    ranked = sorted(signals, key=lambda s: -(abs((s["prob_up"] or 0.5) - 0.5) * (s["conf"] or 0)))
    top_5 = ranked[:5]

    # Score realized 1-week for closed observations
    scoring = score_horizon(records, 5, "1w", price_provider)

    return {
        "week_ending": week_ending,
        "n_signals": len(signals),
        "top_5_conviction": top_5,
        "one_week_accuracy": scoring,
    }
