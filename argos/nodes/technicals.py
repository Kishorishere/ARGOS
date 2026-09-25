"""
Technicals Analyst node.

Computes a full IndicatorSnapshot from the OHLCV window (RSI, MACD, ADX,
Bollinger, Keltner, ATR, OBV, VWAP, CMF, MFI, pivots, candlestick
reversals, divergences, and more) AND fetches a Kronos forecast with
uncertainty when available. The LLM synthesizes both streams into an
AnalystSignal that cites specific indicator values.

Every fired indicator signal is preserved on the returned dict so the
decision journal has the full technical trace next to the LLM's take.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from argos.indicators import compute_snapshot
from argos.indicators.multi_timeframe import compute_multi_timeframe, confluence_score
from argos.model_router import get_llm
from argos.schemas import AnalystSignal
from argos.state import FuseState

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parents[2] / "prompts" / "technicals_analyst.md"

# How many Kronos samples to draw when the endpoint is available.
# Kept small so a slow/free-tier endpoint doesn't stall the pipeline.
KRONOS_UNCERTAINTY_SAMPLES = int(os.environ.get("KRONOS_UNCERTAINTY_SAMPLES", "5"))


def _load_prompt() -> str:
    return PROMPT_PATH.read_text()


def _format_prompt(state: FuseState) -> str:
    return _load_prompt().format(
        ticker=state["ticker"],
        as_of_date=state["as_of_date"],
    )


def _fetch_kronos_forecast(state: FuseState) -> dict | None:
    """Fetch a Kronos forecast with uncertainty bands. Returns None if unreachable."""
    endpoint_url = os.environ.get("KRONOS_ENDPOINT_URL")
    if not endpoint_url:
        logger.warning("KRONOS_ENDPOINT_URL not set — skipping Kronos forecast")
        return None

    ohlcv = state.get("ohlcv_window", {})
    ohlcv_records = ohlcv.get("ohlcv", [])
    if not ohlcv_records:
        logger.warning("No OHLCV data in state — cannot fetch Kronos forecast")
        return None

    try:
        from argos.data.kronos_client import fetch_forecast, fetch_forecast_with_uncertainty

        if KRONOS_UNCERTAINTY_SAMPLES > 1:
            return fetch_forecast_with_uncertainty(
                endpoint_url=endpoint_url,
                ticker=state["ticker"],
                ohlcv_records=ohlcv_records,
                pred_len=10,
                freq="B",
                n_samples=KRONOS_UNCERTAINTY_SAMPLES,
                timeout=30,
            )
        return fetch_forecast(
            endpoint_url=endpoint_url,
            ticker=state["ticker"],
            ohlcv_records=ohlcv_records,
            pred_len=10,
            freq="B",
            timeout=30,
        )
    except Exception as e:
        logger.warning("Kronos forecast failed: %s — proceeding with indicators only", e)
        return None


def _build_kronos_text(forecast: dict | None) -> str:
    if not forecast:
        return (
            "### Kronos Forecast\n"
            "Not available (endpoint offline or no data). Base the signal on the "
            "indicator snapshot alone; cap confidence at ~0.6."
        )

    # ── Prefer calibrated views. Fall back to raw for old journals. ──
    direction = forecast.get("direction_score")
    short_pct = forecast.get("short_horizon_pct")
    swing_pct = forecast.get("swing_horizon_pct")
    shape = forecast.get("shape_regime")
    avg_unc = forecast.get("avg_uncertainty_normalized")
    raw_pct = forecast.get("predicted_move_pct", 0)
    pred_len = forecast.get("pred_len", 10)

    lines = [
        "### Kronos Forecast (CALIBRATED — use direction, not magnitude)",
        f"- **Direction score**: {direction:+.2f}  (range [-1..+1]; sign is a real ~55% edge, magnitude is unreliable)"
        if direction is not None else f"- Raw predicted move: {raw_pct:+.2f}% (uncalibrated — treat as noisy)",
    ]
    if short_pct is not None:
        lines.append(f"- Short horizon (bars 1..3): {short_pct:+.2f}%  → day-trade tilt")
    if swing_pct is not None:
        lines.append(f"- Swing horizon (bars 5..{pred_len}): {swing_pct:+.2f}%  → swing tilt")
    if shape:
        lines.append(f"- Path shape: **{shape}** — {'entering / continuing trend' if shape in ('steady_up','steady_down') else 'reversal / choppy'}")
    if avg_unc is not None:
        if avg_unc < 0.005:
            band = "TIGHT (higher forecast confidence)"
        elif avg_unc < 0.015:
            band = "MODERATE"
        else:
            band = "WIDE (LOW forecast confidence — discount Kronos)"
        lines.append(f"- Uncertainty band: {band} (normalized {avg_unc:.4f})")
    lines.append(
        "\n**Rule**: use Kronos DIRECTION as a tiebreaker aligned with indicator bias. "
        "IGNORE the raw magnitude — an empirically observed 5-14x error range makes it useless "
        "for sizing. When Kronos direction contradicts indicator bias, cap confidence at 0.5."
    )
    return "\n".join(lines)


def technicals_node(state: FuseState) -> dict:
    """Technicals Analyst — indicators + Kronos forecast synthesis."""
    agent_name = "technicals"
    ticker = state["ticker"]
    as_of_date = state["as_of_date"]
    logger.info("Running %s for %s as of %s", agent_name, ticker, as_of_date)

    # Compute the hand-rolled indicator snapshot first — cheap, deterministic,
    # available even without a Kronos endpoint.
    ohlcv_records = state.get("ohlcv_window", {}).get("ohlcv", [])
    snapshot = compute_snapshot(ohlcv_records, ticker=ticker, as_of_date=as_of_date)
    snapshot_dict = snapshot.model_dump()

    # P3: multi-timeframe snapshots — daily, weekly (Fri close), monthly.
    # Confluence across timeframes is a real edge on NEPSE where the retail
    # trend is slow. The forecast node reads this to boost 1w/1m confidence.
    mts = compute_multi_timeframe(ohlcv_records, ticker=ticker, as_of_date=as_of_date)
    confluence = confluence_score(mts)
    snapshot_dict["multi_timeframe"] = {
        tf: snap.model_dump() for tf, snap in mts.items()
    }
    snapshot_dict["confluence"] = confluence
    logger.info(
        "MTF confluence: %d bull / %d bear / %d neutral timeframes, score %+.2f  "
        "%s",
        confluence["n_bullish"], confluence["n_bearish"], confluence["n_neutral"],
        confluence["score"],
        "ALIGNED BULL" if confluence["aligned_bullish"] else
        "ALIGNED BEAR" if confluence["aligned_bearish"] else "",
    )

    try:
        kronos_forecast = _fetch_kronos_forecast(state)

        llm = get_llm(agent_name)
        structured_llm = llm.with_structured_output(AnalystSignal)
        system_prompt = _format_prompt(state)

        user_message = (
            f"Produce a technical signal for {ticker} as of {as_of_date}.\n\n"
            f"{snapshot.to_prompt_text()}\n\n"
            f"{_build_kronos_text(kronos_forecast)}\n\n"
            f"Synthesize both streams. Cite specific indicator values by name in your reasoning."
        )

        result: AnalystSignal = structured_llm.invoke([
            ("system", system_prompt),
            ("user", user_message),
        ])
        signal_dict = result.model_dump()

        # Attach the raw snapshot after the LLM call so the journal has both.
        signal_dict["indicator_snapshot"] = snapshot_dict

        logger.info(
            "%s signal: %s (confidence: %.2f) — %d indicator signals fired, net bias %+.2f",
            agent_name,
            signal_dict["signal"],
            signal_dict["confidence"],
            len(snapshot.fired_signals),
            snapshot.net_bias_score,
        )

        existing_prompts = dict(state.get("analyst_prompts") or {})
        return {
            "technicals_signal": signal_dict,
            "kronos_forecast": kronos_forecast,
            "analyst_prompts": {
                **existing_prompts,
                "technicals": {"system": system_prompt, "user": user_message},
            },
        }

    except Exception as e:
        error_msg = f"{agent_name} node failed: {e}"
        logger.error(error_msg)
        return {
            # Even on LLM failure we still emit the indicator snapshot so the
            # journal, debate and risk manager keep a technical picture.
            "technicals_signal": {
                "signal": "neutral",
                "confidence": 0.0,
                "key_factors": ["technicals LLM failed"],
                "reasoning": f"LLM call failed: {e}",
                "indicator_snapshot": snapshot_dict,
            },
            "errors": [error_msg],
        }
