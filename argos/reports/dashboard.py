"""NEPSE watch — one printable dashboard page."""

from __future__ import annotations

from datetime import date

from argos.reports._common import load_journal_records, score_horizon


def nepse_watch_dashboard(as_of: str | None = None, price_provider=None) -> str:
    """Return the dashboard as a formatted string ready for stdout / mail."""
    if price_provider is None:
        from argos.data.nepse_ohlcv import load_nepse_ohlcv
        price_provider = load_nepse_ohlcv
    if as_of is None:
        as_of = date.today().isoformat()

    lines: list[str] = []
    lines.append("=" * 88)
    lines.append(f"NEPSE WATCH  —  {as_of}")
    lines.append("=" * 88)

    # NEPSE index + breadth
    try:
        from argos.data.nepse_api import market_breadth
        b = market_breadth()
        idx = float(b.get("nepse_index") or 0)
        chg = float(b.get("nepse_change_pct") or 0)
        adv = int(b.get("advances") or 0)
        dec = int(b.get("declines") or 0)
        ad = float(b.get("advance_decline_ratio") or 0)
        turnover = float(b.get("total_turnover_npr") or 0)
        txns = int(b.get("total_transactions") or 0)
        lines.append(f"NEPSE INDEX: {idx:.2f}  ({chg:+.2f}% today)")
        lines.append(f"BREADTH:     {adv} up / {dec} down (A/D {ad:.2f})")
        lines.append(f"TURNOVER:    NPR {turnover/1e6:.1f}M  ({txns} txns)")
    except Exception as e:
        lines.append(f"(market snapshot unavailable: {e})")

    # Macro regime
    try:
        from argos.data.macro_context import current_regime
        regime, reasons = current_regime()
        lines.append(f"MACRO:       {regime.upper()}  ({'; '.join(reasons) if reasons else 'quiet'})")
    except Exception:
        pass

    # Top sector momentum today
    try:
        from argos.data.nepse_sectors import today_sector_scores
        scores = today_sector_scores()
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:3]
        bot = sorted(scores.items(), key=lambda kv: kv[1])[:3]
        lines.append("SECTORS UP:  " + ", ".join(f"{s} {v:+.2f}%" for s, v in top))
        lines.append("SECTORS DN:  " + ", ".join(f"{s} {v:+.2f}%" for s, v in bot))
    except Exception:
        pass

    # High-conviction picks from today's journal
    lines.append("-" * 88)
    lines.append("HIGH-CONVICTION PICKS TODAY  (posterior > 0.70 on any horizon)")
    lines.append("-" * 88)
    records = load_journal_records(since=as_of, until=as_of)
    picks: list[tuple[float, str]] = []
    for r in records:
        mhf = r.get("multi_horizon_forecast") or {}
        for h_key in ("1d", "1w", "1m"):
            hz = (mhf.get("horizons") or {}).get(h_key)
            if not hz:
                continue
            p_up = hz.get("probability_up", 0.5) or 0.5
            conf = hz.get("confidence", 0) or 0
            if conf > 0.65 and (p_up > 0.70 or p_up < 0.30):
                dr = " | ".join(hz.get("key_drivers", [])[:2])
                score = abs(p_up - 0.5) * conf
                picks.append((score, f"  {hz.get('direction').upper():4s} {r['ticker']:8s} "
                                      f"{h_key}  p_up={p_up:.2f} conf={conf:.2f} "
                                      f"exp {hz.get('expected_return_pct', 0):+.2f}%   [{dr}]"))
    picks.sort(key=lambda p: -p[0])
    if picks:
        for _, line in picks[:15]:
            lines.append(line)
    else:
        lines.append("  (no high-conviction setups today)")

    # Rolling 90-day accuracy
    lines.append("-" * 88)
    lines.append("ROLLING 90-DAY ACCURACY")
    lines.append("-" * 88)
    from datetime import datetime, timedelta
    since_90 = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    hist = load_journal_records(since=since_90, until=as_of)
    for h_key, h_days, label in (("1d", 1, "1-day"), ("1w", 5, "1-week"), ("1m", 21, "1-month")):
        s = score_horizon(hist, h_days, h_key, price_provider)
        n = s["n"]
        if n:
            lo, hi = s["ci_95_pct"]
            lines.append(f"  {label:8s}: {s['hit_rate_pct']:5.1f}%   "
                         f"(n={n}, 95% CI [{lo:.1f}%, {hi:.1f}%])")
        else:
            lines.append(f"  {label:8s}: no closed observations yet")

    # Drawdown state
    try:
        from argos.risk.drawdown import drawdown_state
        dd = drawdown_state()
        lines.append("-" * 88)
        lines.append(f"DRAWDOWN:    {dd.drawdown_pct:+.2f}% from peak  "
                     f"({dd.status.upper()})  scale × {dd.size_scale}")
    except Exception:
        pass

    lines.append("=" * 88)
    return "\n".join(lines)
