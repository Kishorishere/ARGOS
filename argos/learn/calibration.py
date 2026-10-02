"""Isotonic calibration for `probability_up` outputs.

Two sources currently emit uncalibrated probabilities:

  1. `argos.learn.xgb_direction` — the LightGBM direction classifier
  2. `argos.nodes.forecast.forecast_node` — the hand-tuned + Bayesian
     ensemble

Both were audited and found to be miscalibrated (item 7b context in the
plan): a p=0.88 output showed roughly the same realized hit rate as a
p=0.60 output. Isotonic regression fits a monotonic step function from
raw probability to empirical hit rate — the minimum-assumption fix.

Two calibrators are trained, from the same walk-forward evidence:

  * ``LGBM_CALIBRATOR_PATH`` — from ``argos.learn.xgb_direction``'s
    walk-forward predictions.
  * ``FORECAST_CALIBRATOR_PATH`` — from the ensemble's history, sourced
    at CLI-fit time by re-running ``scripts/one_week_forecast.py --audit``
    style walk-forward across the cache.

Consumers call ``calibrate_lgbm(p)`` and ``calibrate_forecast(p)``;
either returns the input unchanged if its calibrator hasn't been fit.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
LGBM_PRED_PATH = CACHE_DIR / "lgbm_wf_predictions.pkl"
LGBM_CALIBRATOR_PATH = CACHE_DIR / "lgbm_calibrator.pkl"
FORECAST_PRED_PATH = CACHE_DIR / "forecast_wf_predictions.pkl"
FORECAST_CALIBRATOR_PATH = CACHE_DIR / "forecast_calibrator.pkl"
EVENT_PRED_PATH = CACHE_DIR / "event_wf_predictions.pkl"
EVENT_CALIBRATOR_PATH = CACHE_DIR / "event_calibrator.pkl"

# Tiering thresholds for the event-conditional model. These are what
# `scripts/event_alerts.py` uses to decide silent / BUY / STRONG BUY.
EVENT_BUY_THRESHOLD = 0.65
EVENT_STRONG_BUY_THRESHOLD = 0.80


def event_tier(calibrated_p: float) -> str:
    """Map a calibrated event p_up to one of {"silent","BUY","STRONG BUY"}."""
    if calibrated_p >= EVENT_STRONG_BUY_THRESHOLD:
        return "STRONG BUY"
    if calibrated_p >= EVENT_BUY_THRESHOLD:
        return "BUY"
    return "silent"


def _fit_isotonic(y_true: np.ndarray, p_raw: np.ndarray) -> IsotonicRegression:
    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.02, y_max=0.98)
    ir.fit(p_raw, y_true)
    return ir


def fit_lgbm_calibrator() -> dict | None:
    """Fit LGBM calibrator from its walk-forward predictions. Returns None
    if the predictions file is missing (run --eval on xgb_direction first).
    """
    if not LGBM_PRED_PATH.exists():
        logger.warning("Missing %s — run `python -m argos.learn.xgb_direction --eval` first.",
                       LGBM_PRED_PATH)
        return None
    with open(LGBM_PRED_PATH, "rb") as fh:
        d = pickle.load(fh)
    p = np.array(d["p"])
    y = np.array(d["y"])
    ir = _fit_isotonic(y, p)
    LGBM_CALIBRATOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LGBM_CALIBRATOR_PATH, "wb") as fh:
        pickle.dump({"isotonic": ir, "n_samples": len(y),
                     "base_rate": float(y.mean())}, fh)
    logger.info("LGBM calibrator fit on %d samples (base rate %.3f) -> %s",
                len(y), y.mean(), LGBM_CALIBRATOR_PATH)
    return {"path": str(LGBM_CALIBRATOR_PATH), "n": len(y), "base_rate": float(y.mean())}


def fit_event_calibrator() -> dict | None:
    """Fit calibrator from the event-conditional model's walk-forward
    predictions. Returns None when the predictions file is missing.
    """
    if not EVENT_PRED_PATH.exists():
        logger.warning("Missing %s — run `python -m argos.learn.event_conditional --eval` first.",
                       EVENT_PRED_PATH)
        return None
    with open(EVENT_PRED_PATH, "rb") as fh:
        d = pickle.load(fh)
    p = np.array(d["p"])
    y = np.array(d["y"])
    if len(y) < 20:
        logger.warning("Only %d event WF samples — calibrator will be noisy; "
                       "consider deferring until the panel grows.", len(y))
    ir = _fit_isotonic(y, p)
    EVENT_CALIBRATOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVENT_CALIBRATOR_PATH, "wb") as fh:
        pickle.dump({"isotonic": ir, "n_samples": len(y),
                     "base_rate": float(y.mean())}, fh)
    logger.info("Event calibrator fit on %d samples (base rate %.3f) -> %s",
                len(y), y.mean(), EVENT_CALIBRATOR_PATH)
    return {"path": str(EVENT_CALIBRATOR_PATH), "n": len(y),
            "base_rate": float(y.mean())}


def fit_forecast_calibrator(walk_forward_records: list[dict]) -> dict:
    """Fit forecast calibrator. Expects records with keys `p_up` and `y_up`.
    Persisted separately from the LGBM calibrator so the two consumers
    keep their own maps.
    """
    p = np.array([r["p_up"] for r in walk_forward_records], dtype=float)
    y = np.array([int(r["y_up"]) for r in walk_forward_records])
    ir = _fit_isotonic(y, p)
    FORECAST_CALIBRATOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FORECAST_CALIBRATOR_PATH, "wb") as fh:
        pickle.dump({"isotonic": ir, "n_samples": len(y),
                     "base_rate": float(y.mean())}, fh)
    logger.info("Forecast calibrator fit on %d samples (base rate %.3f) -> %s",
                len(y), y.mean(), FORECAST_CALIBRATOR_PATH)
    return {"path": str(FORECAST_CALIBRATOR_PATH), "n": len(y), "base_rate": float(y.mean())}


# ── Lazy loaders ────────────────────────────────────────────────────────────

_lgbm_cal_bundle = None
_forecast_cal_bundle = None
_event_cal_bundle = None


def _load(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, "rb") as fh:
            return pickle.load(fh)
    except Exception as e:
        logger.warning("Failed to load calibrator %s: %s", path, e)
        return None


def calibrate_lgbm(p: float) -> float:
    """Return the calibrated LGBM probability. Passthrough if no calibrator."""
    global _lgbm_cal_bundle
    if _lgbm_cal_bundle is None:
        _lgbm_cal_bundle = _load(LGBM_CALIBRATOR_PATH)
    if _lgbm_cal_bundle is None:
        return p
    return float(_lgbm_cal_bundle["isotonic"].predict([p])[0])


def calibrate_forecast(p: float) -> float:
    """Return the calibrated forecast_node probability_up. Passthrough if none."""
    global _forecast_cal_bundle
    if _forecast_cal_bundle is None:
        _forecast_cal_bundle = _load(FORECAST_CALIBRATOR_PATH)
    if _forecast_cal_bundle is None:
        return p
    return float(_forecast_cal_bundle["isotonic"].predict([p])[0])


def calibrate_event(p: float) -> float:
    """Return the calibrated event-model p_up. Passthrough if none."""
    global _event_cal_bundle
    if _event_cal_bundle is None:
        _event_cal_bundle = _load(EVENT_CALIBRATOR_PATH)
    if _event_cal_bundle is None:
        return p
    return float(_event_cal_bundle["isotonic"].predict([p])[0])


def clear_cache() -> None:
    global _lgbm_cal_bundle, _forecast_cal_bundle, _event_cal_bundle
    _lgbm_cal_bundle = None
    _forecast_cal_bundle = None
    _event_cal_bundle = None


# ── CLI ────────────────────────────────────────────────────────────────────

def _build_forecast_wf_predictions(months: int = 12) -> list[dict]:
    """Walk the cached OHLCV, forecast at each weekly checkpoint on each
    ticker, pair p_up with realized 5-bar direction. Used only when the
    caller passes --fit-forecast without a pre-built predictions file.
    """
    REPO = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(REPO))
    from argos.data.nepse_ohlcv import load_nepse_ohlcv
    from scripts.one_week_forecast import _forecast_from_records
    from datetime import datetime, timedelta

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    cache_dir = REPO / "data" / "cache" / "nepse_ohlcv"
    records: list[dict] = []
    for pth in sorted(cache_dir.glob("*.pkl")):
        ticker = pth.stem
        try:
            data = load_nepse_ohlcv(ticker, yesterday, lookback_days=2000)
        except Exception:
            continue
        recs = data["ohlcv"]
        last_idx = len(recs) - 6
        step = 5
        start_idx = max(220, last_idx - months * 4 * step)   # 4 weeks/month
        for idx in range(start_idx, last_idx + 1, step):
            as_of = recs[idx]["date"]
            slice_ = recs[: idx + 1]
            try:
                mhf = _forecast_from_records(ticker, slice_, as_of)
            except Exception:
                continue
            h1w = (mhf.get("horizons") or {}).get("1w") or {}
            if not h1w:
                continue
            entry = recs[idx]["close"]
            exit_ = recs[idx + 5]["close"]
            if entry <= 0:
                continue
            records.append({"symbol": ticker, "date": as_of,
                            "p_up": h1w.get("probability_up", 0.5),
                            "y_up": 1 if exit_ > entry else 0})
    # Persist so we don't have to rebuild
    FORECAST_PRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FORECAST_PRED_PATH, "wb") as fh:
        pickle.dump(records, fh)
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit-lgbm", action="store_true",
                    help="Fit LGBM calibrator from lgbm_wf_predictions.pkl")
    ap.add_argument("--fit-forecast", action="store_true",
                    help="Fit forecast calibrator; builds walk-forward if needed")
    ap.add_argument("--fit-event", action="store_true",
                    help="Fit event-model calibrator from event_wf_predictions.pkl "
                         "(run `python -m argos.learn.event_conditional --eval` first)")
    ap.add_argument("--months", type=int, default=12,
                    help="Walk-forward months for --fit-forecast (default 12)")
    ap.add_argument("--print", action="store_true",
                    help="Print calibrated vs raw probability at a grid of inputs")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.fit_lgbm:
        fit_lgbm_calibrator()

    if args.fit_event:
        fit_event_calibrator()

    if args.fit_forecast:
        if FORECAST_PRED_PATH.exists():
            with open(FORECAST_PRED_PATH, "rb") as fh:
                records = pickle.load(fh)
            print(f"Reusing {len(records)} forecast predictions from cache "
                  f"({FORECAST_PRED_PATH.name}).")
        else:
            print(f"Building walk-forward forecasts across cache "
                  f"({args.months} months) ...")
            records = _build_forecast_wf_predictions(args.months)
        fit_forecast_calibrator(records)

    if args.print:
        print()
        print(f"{'raw':>5s}  {'LGBM cal':>10s}  {'fcst cal':>10s}  {'event cal':>10s}  {'tier':>12s}")
        for p in [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]:
            ec = calibrate_event(p)
            print(f"{p:>5.2f}  {calibrate_lgbm(p):>10.3f}  "
                  f"{calibrate_forecast(p):>10.3f}  {ec:>10.3f}  "
                  f"{event_tier(ec):>12s}")


if __name__ == "__main__":
    main()
