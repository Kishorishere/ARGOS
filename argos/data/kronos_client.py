"""
Kronos Forecast Client — HTTP client for the Colab-hosted Kronos model.

Calls the FastAPI endpoint (Cloudflare tunnel) to get price forecasts
with uncertainty estimation. The endpoint runs Kronos-small on a T4 GPU.

Expected endpoint: POST {KRONOS_ENDPOINT_URL}/forecast
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30  # seconds per single forecast call
DEFAULT_PRED_LEN = 10  # number of future bars to forecast
DEFAULT_FREQ = "B"  # business days (Mon-Fri)
DEFAULT_UNCERTAINTY_SAMPLES = 15  # number of calls for uncertainty estimation


def get_kronos_endpoint() -> str | None:
    """Read the Kronos endpoint URL from environment."""
    return os.environ.get("KRONOS_ENDPOINT_URL")


def check_health(endpoint_url: str, timeout: int = 10) -> dict:
    """Check if the Kronos endpoint is alive.

    Returns the health response dict or raises on failure.
    """
    resp = requests.get(f"{endpoint_url}/health", timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _ohlcv_to_candles(ohlcv_records: list[dict]) -> list[dict]:
    """Convert OHLCV records from ohlcv_loader format to Kronos candle format.

    ohlcv_loader uses: {"date": "2024-01-17", "open": ..., "high": ..., ...}
    Kronos expects:    {"timestamp": "2024-01-17", "open": ..., "high": ..., "amount": 0.0, ...}
    """
    candles = []
    for record in ohlcv_records:
        candles.append({
            "timestamp": record["date"],
            "open": float(record["open"]),
            "high": float(record["high"]),
            "low": float(record["low"]),
            "close": float(record["close"]),
            "volume": float(record["volume"]),
            "amount": 0.0,
        })
    return candles


def fetch_forecast(
    endpoint_url: str,
    ticker: str,
    ohlcv_records: list[dict],
    pred_len: int = DEFAULT_PRED_LEN,
    freq: str = DEFAULT_FREQ,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch a single Kronos forecast from the endpoint.

    Args:
        endpoint_url: Base URL of the Kronos FastAPI server.
        ticker: Stock ticker symbol.
        ohlcv_records: List of OHLCV dicts from ohlcv_loader.
        pred_len: Number of future bars to forecast.
        freq: Pandas frequency string ("B" = business days, "D" = calendar).
        timeout: HTTP timeout in seconds.

    Returns:
        Dict with keys: ticker, pred_len, forecast (list of bar dicts),
        last_input_close, predicted_move_pct.
    """
    candles = _ohlcv_to_candles(ohlcv_records)
    last_close = candles[-1]["close"]

    payload = {
        "ticker": ticker,
        "candles": candles,
        "pred_len": pred_len,
        "freq": freq,
    }

    t0 = time.time()
    resp = requests.post(
        f"{endpoint_url}/forecast",
        json=payload,
        timeout=timeout,
    )
    elapsed = time.time() - t0
    resp.raise_for_status()

    result = resp.json()
    forecast = result["forecast"]
    forecast_closes = [bar["close"] for bar in forecast]
    predicted_move = ((forecast_closes[-1] - last_close) / last_close) * 100

    logger.info(
        "Kronos forecast for %s: %d bars, predicted move %.2f%% (%.1fs)",
        ticker,
        len(forecast),
        predicted_move,
        elapsed,
    )

    return {
        "ticker": ticker,
        "pred_len": len(forecast),
        "forecast": forecast,
        "last_input_close": last_close,
        "predicted_move_pct": round(predicted_move, 4),
        "latency_s": round(elapsed, 2),
    }


def fetch_forecast_with_uncertainty(
    endpoint_url: str,
    ticker: str,
    ohlcv_records: list[dict],
    pred_len: int = DEFAULT_PRED_LEN,
    freq: str = DEFAULT_FREQ,
    n_samples: int = DEFAULT_UNCERTAINTY_SAMPLES,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch multiple Kronos forecasts and compute uncertainty statistics.

    Calls the endpoint N times, each producing a single sampled forecast,
    then computes mean, std, p10/p50/p90, and normalized uncertainty
    across all samples.

    This is used by the Technicals Analyst to assess forecast confidence.

    Returns:
        Dict with keys from fetch_forecast() plus:
        - mean_forecast: list of mean close prices per bar
        - std_forecast: list of std dev per bar
        - uncertainty_p10, uncertainty_p50, uncertainty_p90: percentile bands
        - uncertainty_normalized: std / |mean| per bar
        - n_samples: number of samples actually collected
    """
    import numpy as np

    all_close_paths = []
    total_latency = 0.0
    last_forecast = None

    for i in range(n_samples):
        try:
            result = fetch_forecast(
                endpoint_url=endpoint_url,
                ticker=ticker,
                ohlcv_records=ohlcv_records,
                pred_len=pred_len,
                freq=freq,
                timeout=timeout,
            )
            closes = [bar["close"] for bar in result["forecast"]]
            all_close_paths.append(closes)
            total_latency += result["latency_s"]
            last_forecast = result
        except Exception as e:
            logger.warning("Kronos sample %d/%d failed: %s", i + 1, n_samples, e)
            continue

    if not all_close_paths:
        raise RuntimeError(f"All {n_samples} Kronos forecast calls failed for {ticker}")

    paths = np.array(all_close_paths)  # shape: (n_collected, pred_len)
    n_collected = paths.shape[0]

    mean_path = paths.mean(axis=0).tolist()
    std_path = paths.std(axis=0).tolist()
    p10 = np.percentile(paths, 10, axis=0).tolist()
    p50 = np.percentile(paths, 50, axis=0).tolist()
    p90 = np.percentile(paths, 90, axis=0).tolist()
    unc_normalized = [
        s / (abs(m) + 1e-8) for s, m in zip(std_path, mean_path)
    ]

    last_close = _ohlcv_to_candles(ohlcv_records)[-1]["close"]
    predicted_move = ((mean_path[-1] - last_close) / last_close) * 100

    # ── Calibrated multi-horizon views ─────────────────────────────────
    # Empirically Kronos direction is ~50-60% right, magnitude ratio has been
    # observed 5-14x off. So we surface:
    #   - direction_score in [-1, +1]  (sign preserved, magnitude clamped)
    #   - short_horizon_pct  (mean move bars 1..3)   for day-trade decisions
    #   - swing_horizon_pct  (mean move bars 5..N)   for swing decisions
    #   - shape_regime       classified path type
    #   - avg_uncertainty    normalized band width
    def _clamp(x, lo=-1.0, hi=1.0):
        return max(lo, min(hi, x))

    def _to_pct(paths, start_idx, end_idx):
        segment = mean_path[start_idx:end_idx]
        if not segment:
            return None
        return ((sum(segment) / len(segment)) / last_close - 1) * 100

    short_pct = _to_pct(mean_path, 0, min(3, pred_len))
    swing_pct = _to_pct(mean_path, min(4, pred_len), pred_len)
    direction_score = float(_clamp(np.sign(predicted_move) * min(abs(predicted_move) / 2.0, 1.0)))
    avg_unc = float(np.mean(unc_normalized))

    # Shape classifier — look at slope of first vs second half
    first_half_slope = (mean_path[len(mean_path)//2] - mean_path[0]) if len(mean_path) >= 2 else 0
    second_half_slope = (mean_path[-1] - mean_path[len(mean_path)//2]) if len(mean_path) >= 2 else 0
    if first_half_slope > 0 and second_half_slope > 0:
        shape = "steady_up"
    elif first_half_slope < 0 and second_half_slope < 0:
        shape = "steady_down"
    elif first_half_slope < 0 and second_half_slope > 0:
        shape = "v_bottom"
    elif first_half_slope > 0 and second_half_slope < 0:
        shape = "inverted_v"
    else:
        shape = "chop"

    logger.info(
        "Kronos %s: %d/%d samples, mean move %.2f%%, "
        "direction_score %+.2f (short %+.2f%% / swing %+.2f%%, %s), unc %.4f (%.1fs)",
        ticker, n_collected, n_samples, predicted_move,
        direction_score, short_pct or 0, swing_pct or 0, shape, avg_unc, total_latency,
    )

    return {
        "ticker": ticker,
        "pred_len": pred_len,
        "mean_forecast": mean_path,
        "std_forecast": std_path,
        "uncertainty_p10": p10,
        "uncertainty_p50": p50,
        "uncertainty_p90": p90,
        "uncertainty_normalized": unc_normalized,
        "last_input_close": last_close,
        # Raw 10-bar cumulative — kept for backward compatibility. USE WITH CARE:
        # empirically off by 5-14x. Prefer direction_score below.
        "predicted_move_pct": round(predicted_move, 4),
        # Calibrated views — feed these to the LLM.
        "direction_score": round(direction_score, 4),
        "short_horizon_pct": round(short_pct, 4) if short_pct is not None else None,
        "swing_horizon_pct": round(swing_pct, 4) if swing_pct is not None else None,
        "shape_regime": shape,
        "avg_uncertainty_normalized": round(avg_unc, 4),
        "n_samples": n_collected,
        "total_latency_s": round(total_latency, 2),
    }
