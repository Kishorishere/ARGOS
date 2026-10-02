"""1-week directional classifier — panel LightGBM over the cached universe.

Target: sign(close[t+5] / close[t] - 1). Binary classification of the
5-trading-day-ahead close-to-close direction. This is the same problem
the Adhikari 2024 NEPSE paper attacked with XGBoost (they reported
65.15% walk-forward, which is our headline number to beat).

Design choices matched to the plan:

  - **Common overlap window.** We stack every cached ticker's history but
    truncate to dates where at least 8 of 11 tickers have data. This
    yields ~600 training rows per ticker × 11 = ~6.6k samples, all
    contemporaneous, without leaking data from a longer-history ticker
    into a shorter one's fold.
  - **Panel model.** One LightGBM classifier with `symbol` as a
    categorical feature so the model can share regularization across
    tickers (the NABIL=40% / ADBL=57% asymmetry both forks called out).
  - **Walk-forward eval.** Expanding window, 260-day train / 20-day
    test, 6 folds. Report per-ticker directional accuracy + Brier score.
  - **Persisted artefacts.** Model + feature names go to
    `data/cache/lgbm_direction_1w.pkl` for lazy load in the ensemble
    (item 7c).

Run as a module: ``python -m argos.learn.xgb_direction --eval``.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import statistics
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from argos.indicators import compute_snapshot
from argos.indicators.multi_timeframe import compute_multi_timeframe, confluence_score

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "nepse_ohlcv"
MODEL_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "lgbm_direction_1w.pkl"
FEATURE_MATRIX_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "lgbm_features_1w.pkl"

LABEL_LOOKAHEAD_BARS = 5           # 1-trading-week
MIN_TICKERS_PER_DATE = 1           # any date with at least 1 warmed-up ticker
MIN_BARS_FOR_SNAPSHOT = 220        # snapshot warm-up (matches engine)

# Folds are counted in unique training-panel dates. The panel is sparse
# (~1 year of common-overlap dates) so folds are smaller than the plan's
# original 260/20/20 shape — we downsize proportionally.
TRAIN_BARS_PER_FOLD = 60           # dates
TEST_BARS_PER_FOLD = 10
FOLD_STEP_BARS = 10


# ── Feature extraction ─────────────────────────────────────────────────────

_FEATURE_KEYS: list[str] = []       # populated on first extraction call


def _load_records(symbol: str) -> list[dict]:
    p = CACHE_DIR / f"{symbol}.pkl"
    df = pd.read_pickle(p)
    out = []
    for _, row in df.sort_values("date").iterrows():
        out.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        })
    return out


def _flatten_snapshot(snap_dict: dict) -> dict[str, float]:
    """Pluck the numeric fields we want as features. Nested dicts get
    flattened by group name (`momentum.rsi_14` etc). NaN/None → 0.0 with
    a paired `_missing` flag so the tree knows the value was absent.
    """
    out: dict[str, float] = {}
    for group in ("momentum", "trend", "volatility", "volume", "structure"):
        sub = snap_dict.get(group) or {}
        for k, v in sub.items():
            if k == "signals":
                continue
            key = f"{group}.{k}"
            if isinstance(v, (int, float, np.number)) and not (isinstance(v, float) and np.isnan(v)):
                out[key] = float(v)
                out[f"{key}_missing"] = 0.0
            elif isinstance(v, bool):
                out[key] = 1.0 if v else 0.0
                out[f"{key}_missing"] = 0.0
            else:
                out[key] = 0.0
                out[f"{key}_missing"] = 1.0
    # Top-level scalars
    out["net_bias_score"] = float(snap_dict.get("net_bias_score") or 0.0)
    out["composite_bull_score"] = float(snap_dict.get("composite_bull_score") or 0.0)
    out["composite_bear_score"] = float(snap_dict.get("composite_bear_score") or 0.0)
    return out


def _build_feature_row(records: list[dict], idx: int, symbol: str) -> dict | None:
    """Compute one (symbol, date) feature row using bars up to and including idx.

    Returns None if there aren't enough bars for a valid snapshot.
    """
    if idx + 1 < MIN_BARS_FOR_SNAPSHOT:
        return None
    slice_ = records[: idx + 1]
    as_of = records[idx]["date"]
    snap = compute_snapshot(slice_, symbol, as_of).model_dump()
    row = _flatten_snapshot(snap)
    # Multi-timeframe confluence — cheap to compute on the same slice
    try:
        mts = compute_multi_timeframe(slice_, symbol, as_of)
        conf = confluence_score(mts)
        row["mtf_confluence_score"] = float(conf.get("score") or 0.0)
        row["mtf_n_bullish"] = float(conf.get("n_bullish") or 0)
        row["mtf_n_bearish"] = float(conf.get("n_bearish") or 0)
        row["mtf_aligned_bullish"] = 1.0 if conf.get("aligned_bullish") else 0.0
        row["mtf_aligned_bearish"] = 1.0 if conf.get("aligned_bearish") else 0.0
    except Exception:
        row["mtf_confluence_score"] = 0.0
    return row


def build_panel(force: bool = False) -> pd.DataFrame:
    """Build (or load) the training panel across all cached tickers.

    Columns: `symbol` (categorical), `date`, all flattened features, `y`
    (binary sign of 5-bar-ahead return), `ret_5` (raw, for diagnostics).
    """
    if not force and FEATURE_MATRIX_PATH.exists():
        logger.info("Loading cached feature matrix from %s", FEATURE_MATRIX_PATH)
        return pd.read_pickle(FEATURE_MATRIX_PATH)

    symbols = sorted(p.stem for p in CACHE_DIR.glob("*.pkl"))
    if not symbols:
        raise RuntimeError(f"No OHLCV pickles in {CACHE_DIR}")

    # ── Common-overlap gate: keep dates where >= MIN_TICKERS_PER_DATE
    #   tickers have >= MIN_BARS_FOR_SNAPSHOT bars up to that date.
    per_symbol_records = {sym: _load_records(sym) for sym in symbols}
    all_dates_by_symbol = {sym: [r["date"] for r in recs]
                           for sym, recs in per_symbol_records.items()}
    all_dates = sorted({d for lst in all_dates_by_symbol.values() for d in lst})

    def _n_ready_at(d: str) -> int:
        n = 0
        for sym, dates in all_dates_by_symbol.items():
            i = _find_idx(dates, d)
            if i is not None and i + 1 >= MIN_BARS_FOR_SNAPSHOT:
                n += 1
        return n

    eligible_dates = [d for d in all_dates if _n_ready_at(d) >= MIN_TICKERS_PER_DATE]
    if not eligible_dates:
        raise RuntimeError("No eligible dates — check that caches have enough warm-up history.")
    logger.info("Common overlap window: %s .. %s (%d dates, >= %d/11 tickers)",
                eligible_dates[0], eligible_dates[-1], len(eligible_dates),
                MIN_TICKERS_PER_DATE)

    rows: list[dict] = []
    for sym, recs in per_symbol_records.items():
        dates = [r["date"] for r in recs]
        idx_map = {d: i for i, d in enumerate(dates)}
        for d in eligible_dates:
            i = idx_map.get(d)
            if i is None:
                continue
            # Need at least LOOKAHEAD_BARS ahead in this ticker's history
            if i + LABEL_LOOKAHEAD_BARS >= len(recs):
                continue
            feats = _build_feature_row(recs, i, sym)
            if feats is None:
                continue
            entry = recs[i]["close"]
            exit_ = recs[i + LABEL_LOOKAHEAD_BARS]["close"]
            if entry <= 0:
                continue
            ret_5 = exit_ / entry - 1
            row = {"symbol": sym, "date": d, **feats,
                   "ret_5": ret_5, "y": int(ret_5 > 0)}
            rows.append(row)

    panel = pd.DataFrame(rows)
    # Persist feature schema (sorted; symbol/date/y/ret_5 not features)
    global _FEATURE_KEYS
    _FEATURE_KEYS = sorted([c for c in panel.columns
                             if c not in ("symbol", "date", "y", "ret_5")])
    FEATURE_MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    panel.to_pickle(FEATURE_MATRIX_PATH)
    logger.info("Built panel: %d rows, %d features → %s",
                len(panel), len(_FEATURE_KEYS), FEATURE_MATRIX_PATH)
    return panel


def _find_idx(sorted_dates: list[str], target: str) -> int | None:
    lo, hi = 0, len(sorted_dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_dates[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    if lo < len(sorted_dates) and sorted_dates[lo] == target:
        return lo
    return None


# ── Model ──────────────────────────────────────────────────────────────────

def _default_lgb_params() -> dict:
    return {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 6,
        "min_data_in_leaf": 20,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
    }


def _train_one_fold(train_df: pd.DataFrame, feat_cols: list[str]):
    import lightgbm as lgb
    x = train_df[feat_cols].values
    y = train_df["y"].values
    sym = train_df["symbol"].astype("category")
    ds = lgb.Dataset(x, label=y, categorical_feature=[])
    booster = lgb.train(_default_lgb_params(), ds, num_boost_round=300)
    return booster


def _brier(y_true: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y_true) ** 2))


def _reliability_bins(y_true: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict]:
    bins = np.linspace(0, 1, n_bins + 1)
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (p >= lo) & (p < hi)
        if mask.sum() == 0:
            continue
        out.append({
            "bin_low": float(lo),
            "bin_high": float(hi),
            "n": int(mask.sum()),
            "mean_p": float(p[mask].mean()),
            "actual_hit_rate": float(y_true[mask].mean()),
        })
    return out


def walk_forward_eval(panel: pd.DataFrame) -> dict:
    """Expanding-window walk-forward. Returns per-fold and per-ticker
    directional accuracy + Brier + a reliability curve.
    """
    if panel.empty:
        raise RuntimeError("Empty panel — nothing to train.")
    dates = sorted(panel["date"].unique())
    if len(dates) < TRAIN_BARS_PER_FOLD + TEST_BARS_PER_FOLD:
        raise RuntimeError(f"Not enough date coverage: {len(dates)} < "
                           f"{TRAIN_BARS_PER_FOLD + TEST_BARS_PER_FOLD}")
    feat_cols = sorted([c for c in panel.columns
                         if c not in ("symbol", "date", "y", "ret_5")])

    folds: list[dict] = []
    per_ticker_hits: dict[str, list[int]] = {}
    per_ticker_seen: dict[str, int] = {}
    all_p: list[float] = []
    all_y: list[int] = []

    train_end = TRAIN_BARS_PER_FOLD
    while train_end + TEST_BARS_PER_FOLD <= len(dates):
        test_end = min(train_end + TEST_BARS_PER_FOLD, len(dates))
        train_dates = set(dates[:train_end])
        test_dates = set(dates[train_end:test_end])
        train_df = panel[panel["date"].isin(train_dates)]
        test_df = panel[panel["date"].isin(test_dates)]
        if train_df.empty or test_df.empty:
            train_end += FOLD_STEP_BARS
            continue

        booster = _train_one_fold(train_df, feat_cols)
        p = booster.predict(test_df[feat_cols].values)
        y = test_df["y"].values
        preds_dir = (p >= 0.5).astype(int)
        hits = (preds_dir == y).astype(int)

        fold_stats = {
            "train_dates": (dates[0], dates[train_end - 1]),
            "test_dates": (dates[train_end], dates[test_end - 1]),
            "n_train": len(train_df),
            "n_test": len(test_df),
            "hit_rate": float(hits.mean()),
            "brier": _brier(y, p),
        }
        folds.append(fold_stats)

        # Per-ticker
        for sym in test_df["symbol"].unique():
            mask = (test_df["symbol"] == sym).values
            per_ticker_hits.setdefault(sym, []).extend(hits[mask].tolist())
            per_ticker_seen[sym] = per_ticker_seen.get(sym, 0) + int(mask.sum())

        all_p.extend(p.tolist())
        all_y.extend(y.tolist())

        train_end += FOLD_STEP_BARS

    per_ticker = {
        sym: {"n": per_ticker_seen[sym],
              "hit_rate": statistics.mean(per_ticker_hits[sym])
              if per_ticker_hits[sym] else 0.0}
        for sym in sorted(per_ticker_seen)
    }
    all_p_arr = np.array(all_p)
    all_y_arr = np.array(all_y)

    return {
        "n_folds": len(folds),
        "folds": folds,
        "mean_hit_rate": statistics.mean(f["hit_rate"] for f in folds) if folds else 0.0,
        "mean_brier": statistics.mean(f["brier"] for f in folds) if folds else 0.0,
        "per_ticker": per_ticker,
        "reliability": _reliability_bins(all_y_arr, all_p_arr),
        "raw_predictions": {"p": all_p, "y": all_y},
    }


def train_final(panel: pd.DataFrame):
    """Train the production model on the entire panel (no test hold-out).

    The walk-forward eval already gave us the honest OOS number; this
    model is what ships to the ensemble.
    """
    feat_cols = sorted([c for c in panel.columns
                         if c not in ("symbol", "date", "y", "ret_5")])
    booster = _train_one_fold(panel, feat_cols)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as fh:
        pickle.dump({"booster": booster, "features": feat_cols,
                     "trained_rows": len(panel)}, fh)
    logger.info("Wrote final model → %s (%d features, %d rows)",
                MODEL_PATH, len(feat_cols), len(panel))
    return booster, feat_cols


def load_model():
    """Load persisted model + feature list. Returns None if missing."""
    if not MODEL_PATH.exists():
        return None
    with open(MODEL_PATH, "rb") as fh:
        return pickle.load(fh)


def predict_direction(symbol: str, as_of_date: str) -> float | None:
    """Return calibrated (raw) p(up) for one (symbol, as_of_date).

    Loads the OHLCV cache, computes features, runs the model. Returns None
    if the model isn't persisted yet or the symbol has no data.
    """
    bundle = load_model()
    if bundle is None:
        return None
    recs = _load_records(symbol)
    idx_map = {r["date"]: i for i, r in enumerate(recs)}
    i = idx_map.get(as_of_date)
    if i is None:
        return None
    row = _build_feature_row(recs, i, symbol)
    if row is None:
        return None
    feat_cols = bundle["features"]
    x = np.array([[float(row.get(c, 0.0)) for c in feat_cols]])
    p = float(bundle["booster"].predict(x)[0])
    return p


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="Rebuild feature matrix from scratch")
    ap.add_argument("--eval", action="store_true", help="Walk-forward eval only")
    ap.add_argument("--train", action="store_true", help="Fit final model on full panel + persist")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    panel = build_panel(force=args.build)
    print(f"Panel: {len(panel)} rows, {panel['symbol'].nunique()} tickers, "
          f"dates {panel['date'].min()} .. {panel['date'].max()}")

    if args.eval or not (args.train or args.build):
        report = walk_forward_eval(panel)
        print()
        print(f"=== Walk-forward eval: {report['n_folds']} folds ===")
        print(f"Mean hit rate ..... {report['mean_hit_rate']:.3f} "
              f"(baseline 50%, NEPSE paper 65.15%)")
        print(f"Mean Brier score .. {report['mean_brier']:.3f} "
              f"(lower = better calibrated; 0.25 = coin flip)")
        print()
        print("Per-ticker hit rate:")
        for sym, s in report["per_ticker"].items():
            print(f"  {sym:6s}  n={s['n']:>4d}  hit={s['hit_rate']:.3f}")
        print()
        print("Reliability curve (p_up bin -> actual hit rate):")
        for b in report["reliability"]:
            print(f"  p in [{b['bin_low']:.1f},{b['bin_high']:.1f})  "
                  f"n={b['n']:>3d}  mean_p={b['mean_p']:.3f}  "
                  f"actual={b['actual_hit_rate']:.3f}")

        # Persist raw predictions for the isotonic calibrator (item 7b)
        rp = report.pop("raw_predictions")
        rp_path = MODEL_PATH.parent / "lgbm_wf_predictions.pkl"
        with open(rp_path, "wb") as fh:
            pickle.dump(rp, fh)
        print(f"\nWalk-forward predictions saved → {rp_path}")

    if args.train:
        train_final(panel)


if __name__ == "__main__":
    main()
