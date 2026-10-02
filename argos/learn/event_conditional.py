"""Event-conditional LightGBM classifier — the 80% oracle.

Only fits and predicts on rows anchored to a hard event: dividend
announcement, quarterly earnings release, broker cluster buy, SEBON
promoter prior notice. This is a narrow-precision, rare-fire model —
not a broad daily forecaster. The plan's expected coverage is 30-80
signals per year across the 11-ticker universe.

Design decisions match the plan (see plan file):

  - **Label:** triple-barrier at ±1×ATR, timeout 10 bars. Matches the
    2-week (10-trading-day) horizon. `TripleBarrierLabel.hit_up = 1`,
    `hit_down = 0`. Timeout-only rows are labelled by sign of the
    close-to-close return over the same window.
  - **CV:** Purged K-Fold with 10-bar embargo (López de Prado, chapter 7).
    Prevents label-window leakage between train and test.
  - **Model:** LightGBM, `max_depth=4`, `n_estimators=200`,
    `min_data_in_leaf=8` — shallow because the panel is thin.
  - **Reporting:** per-event-type hit rate + calibrated reliability
    curve + coverage (fires-per-year).

Run: ``python -m argos.learn.event_conditional --build --eval --train``.
"""

from __future__ import annotations

import argparse
import logging
import pickle
import statistics
from pathlib import Path

import numpy as np
import pandas as pd

from argos.events import EventRecord, load_events
from argos.indicators import compute_snapshot
from argos.learn.xgb_direction import _flatten_snapshot, _load_records

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
FEATURE_PATH = CACHE_DIR / "event_features_2w.pkl"
MODEL_PATH = CACHE_DIR / "event_lgbm_2w.pkl"
WF_PRED_PATH = CACHE_DIR / "event_wf_predictions.pkl"

LABEL_HORIZON_BARS = 10                # 2 trading weeks
BARRIER_ATR_MULT = 1.0                  # +/- 1x ATR triple-barrier
EMBARGO_BARS = 10                       # match the label horizon
KFOLD_SPLITS = 5

# Event windows: fire the classifier on any bar within `n_bars` of an
# actual event; a fresh event dominates a stale one.
FRESHNESS_BARS = 5

EVENT_TYPES = ("dividend", "earnings", "broker_cluster", "sebon_promoter")


# ── Panel build ────────────────────────────────────────────────────────────

def _events_to_bar_rows(records: list[dict], events: list[EventRecord]) -> list[dict]:
    """For each event, find the trading bar at (or immediately after)
    event_date in `records`. Skip events with no matchable bar or without
    enough forward history for a label.
    """
    date_idx = {r["date"]: i for i, r in enumerate(records)}
    dates_sorted = [r["date"] for r in records]
    out: list[dict] = []
    for ev in events:
        if not ev.event_date:
            continue
        idx = date_idx.get(ev.event_date)
        if idx is None:
            # Snap forward to the next available trading bar within 5 days
            for lookahead in range(1, 6):
                d = (pd.to_datetime(ev.event_date) + pd.Timedelta(days=lookahead)).strftime("%Y-%m-%d")
                if d in date_idx:
                    idx = date_idx[d]
                    break
            if idx is None:
                continue
        if idx + LABEL_HORIZON_BARS >= len(records):
            continue
        out.append({"event": ev, "idx": idx, "entry_date": dates_sorted[idx]})
    return out


def _triple_barrier_label(records: list[dict], idx: int,
                           atr_pct: float) -> tuple[int, float, str]:
    """Return (y, ret_pct, exit_reason).

    y = 1 iff price hit upper barrier (or timed-out with positive close).
    y = 0 iff price hit lower barrier (or timed-out with non-positive close).
    """
    entry = records[idx]["close"]
    if entry <= 0 or atr_pct <= 0:
        return 0, 0.0, "invalid"
    barrier_abs = entry * atr_pct * BARRIER_ATR_MULT / 100  # atr_pct is %
    upper = entry + barrier_abs
    lower = entry - barrier_abs
    for j in range(1, LABEL_HORIZON_BARS + 1):
        bar = records[idx + j]
        if bar["high"] >= upper:
            return 1, (upper / entry - 1) * 100, "upper"
        if bar["low"] <= lower:
            return 0, (lower / entry - 1) * 100, "lower"
    exit_price = records[idx + LABEL_HORIZON_BARS]["close"]
    ret = (exit_price / entry - 1) * 100
    return (1 if ret > 0 else 0), ret, "timeout"


def _one_hot_event_type(et: str) -> dict[str, float]:
    return {f"event_is_{t}": (1.0 if t == et else 0.0) for t in EVENT_TYPES}


def _sector_20bar_return(symbol: str, as_of: str) -> float:
    """Best-effort sector 20-bar return. Silent 0.0 fallback."""
    try:
        from argos.data.nepse_sectors import symbol_to_sector, sector_relative_strength
        sector = symbol_to_sector(symbol)
        if not sector:
            return 0.0
        rs = sector_relative_strength(sector, window=20)
        return float(rs.get("relative_strength_pct") or 0.0) if rs else 0.0
    except Exception:
        return 0.0


def _days_since_prev_same_event(events: list[EventRecord], target: EventRecord) -> float:
    """Sessions elapsed since the previous event of the same type on this
    symbol, or a large sentinel value if none exists."""
    prior = [e for e in events
             if e.symbol == target.symbol
             and e.event_type == target.event_type
             and e.event_date and e.event_date < target.event_date]
    if not prior:
        return 999.0
    latest = max(e.event_date for e in prior)
    return float((pd.to_datetime(target.event_date) - pd.to_datetime(latest)).days)


def build_panel(force: bool = False,
                include_broker_cluster: bool = True) -> pd.DataFrame:
    """Build (or load) the event-anchored training panel.

    Rows: one per (symbol, event) that has a resolvable trading bar and
    ≥10 bars of forward history. Features: flattened IndicatorSnapshot +
    event_type one-hot + magnitude + sector 20b RS + days_since_prev.
    Label: triple-barrier at ±1×ATR / 10-bar.
    """
    if not force and FEATURE_PATH.exists():
        logger.info("Loading cached event panel from %s", FEATURE_PATH)
        return pd.read_pickle(FEATURE_PATH)

    cache_dir = Path(__file__).resolve().parents[2] / "data" / "cache" / "nepse_ohlcv"
    symbols = sorted(p.stem for p in cache_dir.glob("*.pkl"))
    if not symbols:
        raise RuntimeError("no cached OHLCV pickles found")

    all_events = load_events(include_broker_cluster=False)   # cheap bulk
    per_symbol_events: dict[str, list[EventRecord]] = {s: [] for s in symbols}
    for ev in all_events:
        if ev.symbol in per_symbol_events:
            per_symbol_events[ev.symbol].append(ev)

    if include_broker_cluster:
        for sym in symbols:
            try:
                per_symbol_events[sym].extend(
                    load_events(symbol=sym, include_broker_cluster=True,
                                sources=["broker_cluster"])
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("broker_cluster events failed for %s: %s", sym, e)

    rows: list[dict] = []
    for sym in symbols:
        events = sorted(per_symbol_events[sym],
                        key=lambda e: (e.event_date or "", e.event_type))
        if not events:
            continue
        records = _load_records(sym)
        rows_for_sym = _events_to_bar_rows(records, events)
        for r in rows_for_sym:
            ev: EventRecord = r["event"]
            idx: int = r["idx"]
            as_of = records[idx]["date"]
            slice_ = records[: idx + 1]
            if len(slice_) < 220:   # snapshot warm-up
                continue
            snap = compute_snapshot(slice_, sym, as_of).model_dump()
            feats = _flatten_snapshot(snap)
            feats.update(_one_hot_event_type(ev.event_type))
            feats["event_magnitude"] = float(ev.magnitude)
            feats["event_direction_hint"] = float(ev.direction_hint)
            feats["sector_20b_rs"] = _sector_20bar_return(sym, as_of)
            feats["days_since_prev_same_event"] = _days_since_prev_same_event(events, ev)

            atr_pct = (snap.get("volatility") or {}).get("atr_pct") or 0.0
            y, ret_pct, exit_reason = _triple_barrier_label(records, idx, atr_pct)

            rows.append({
                "symbol": sym,
                "event_date": ev.event_date,
                "entry_date": as_of,
                "event_type": ev.event_type,
                "source_id": ev.source_id,
                **feats,
                "atr_pct": atr_pct,
                "ret_2w_pct": ret_pct,
                "exit_reason": exit_reason,
                "y": int(y),
            })

    if not rows:
        raise RuntimeError("no event-anchored rows built — check the "
                           "announcements cache is populated.")

    panel = pd.DataFrame(rows).sort_values("entry_date").reset_index(drop=True)
    FEATURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    panel.to_pickle(FEATURE_PATH)
    logger.info("Event panel: %d rows across %d tickers, %d event types",
                len(panel), panel["symbol"].nunique(), panel["event_type"].nunique())
    return panel


# ── Purged K-Fold ──────────────────────────────────────────────────────────

def _purged_kfold_splits(entry_dates: pd.Series, n_splits: int,
                          embargo_bars: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return (train_idx, test_idx) tuples with an embargo around each
    test fold. Simplified López de Prado — since our label horizon is
    fixed at `LABEL_HORIZON_BARS`, we purge any train row whose entry
    falls within `LABEL_HORIZON_BARS + embargo_bars` calendar days of
    any test row's entry.
    """
    idx_dates = pd.to_datetime(entry_dates.values)
    n = len(idx_dates)
    order = np.argsort(idx_dates)
    fold_sizes = np.full(n_splits, n // n_splits, dtype=int)
    fold_sizes[: n % n_splits] += 1

    embargo = pd.Timedelta(days=LABEL_HORIZON_BARS + embargo_bars)

    out = []
    cursor = 0
    for fs in fold_sizes:
        test_ord = order[cursor: cursor + fs]
        cursor += fs
        test_idx = np.sort(test_ord)
        test_dates = idx_dates[test_idx]
        if len(test_dates) == 0:
            continue
        lo, hi = test_dates.min() - embargo, test_dates.max() + embargo
        train_idx = np.array([
            i for i in range(n)
            if i not in set(test_idx.tolist()) and not (lo <= idx_dates[i] <= hi)
        ])
        if len(train_idx) == 0 or len(test_idx) == 0:
            continue
        out.append((train_idx, test_idx))
    return out


# ── Eval ───────────────────────────────────────────────────────────────────

_FEATURE_EXCLUDE = {"symbol", "event_date", "entry_date", "event_type",
                    "source_id", "ret_2w_pct", "exit_reason", "y", "atr_pct"}


def _feature_cols(panel: pd.DataFrame) -> list[str]:
    return sorted([c for c in panel.columns if c not in _FEATURE_EXCLUDE])


def _train_booster(train_df: pd.DataFrame, feat_cols: list[str]):
    import lightgbm as lgb
    ds = lgb.Dataset(train_df[feat_cols].values, label=train_df["y"].values)
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 15,
        "max_depth": 4,
        "min_data_in_leaf": 8,
        "feature_fraction": 0.8,
        "verbose": -1,
    }
    return lgb.train(params, ds, num_boost_round=200)


def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def walk_forward_eval(panel: pd.DataFrame) -> dict:
    if len(panel) < KFOLD_SPLITS * 4:
        raise RuntimeError(f"panel too small ({len(panel)}) for {KFOLD_SPLITS}-fold CV")

    feat_cols = _feature_cols(panel)
    splits = _purged_kfold_splits(panel["entry_date"], KFOLD_SPLITS, EMBARGO_BARS)

    all_p: list[float] = []
    all_y: list[int] = []
    all_type: list[str] = []

    fold_hits = []
    for i, (tr, te) in enumerate(splits, 1):
        booster = _train_booster(panel.iloc[tr], feat_cols)
        p = booster.predict(panel.iloc[te][feat_cols].values)
        y = panel.iloc[te]["y"].values
        preds = (p >= 0.5).astype(int)
        hit = float((preds == y).mean())
        fold_hits.append(hit)
        all_p.extend(p.tolist())
        all_y.extend(y.tolist())
        all_type.extend(panel.iloc[te]["event_type"].tolist())
        logger.info("fold %d: n_train=%d n_test=%d hit=%.3f", i, len(tr), len(te), hit)

    all_p = np.array(all_p)
    all_y = np.array(all_y)
    all_type = np.array(all_type)

    # Per-event-type
    by_type = {}
    for t in np.unique(all_type):
        mask = all_type == t
        preds = (all_p[mask] >= 0.5).astype(int)
        by_type[t] = {
            "n": int(mask.sum()),
            "hit_rate": float((preds == all_y[mask]).mean()),
            "brier": _brier(all_y[mask], all_p[mask]),
        }

    # Top-decile subset (the "80% oracle" tier)
    decile_thr = float(np.quantile(all_p, 0.9)) if len(all_p) >= 10 else 0.8
    top_mask = all_p >= decile_thr
    top_hit = float(((all_p[top_mask] >= 0.5).astype(int) == all_y[top_mask]).mean()) \
        if top_mask.sum() > 0 else float("nan")

    # Reliability bins
    bins = np.linspace(0, 1, 11)
    rel = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (all_p >= lo) & (all_p < hi)
        if m.sum() == 0:
            continue
        rel.append({"bin_low": float(lo), "bin_high": float(hi),
                    "n": int(m.sum()),
                    "mean_p": float(all_p[m].mean()),
                    "actual": float(all_y[m].mean())})

    return {
        "n_rows": len(panel),
        "n_folds": len(splits),
        "fold_hits": fold_hits,
        "mean_hit_rate": statistics.mean(fold_hits) if fold_hits else 0.0,
        "top_decile_threshold": decile_thr,
        "top_decile_n": int(top_mask.sum()),
        "top_decile_hit_rate": top_hit,
        "per_event_type": by_type,
        "reliability": rel,
        "raw_predictions": {"p": all_p.tolist(), "y": all_y.tolist(),
                            "event_type": all_type.tolist()},
    }


def train_final(panel: pd.DataFrame):
    feat_cols = _feature_cols(panel)
    booster = _train_booster(panel, feat_cols)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as fh:
        pickle.dump({"booster": booster, "features": feat_cols,
                     "trained_rows": len(panel)}, fh)
    logger.info("Wrote event model -> %s (%d rows, %d features)",
                MODEL_PATH, len(panel), len(feat_cols))
    return booster, feat_cols


def load_model():
    if not MODEL_PATH.exists():
        return None
    with open(MODEL_PATH, "rb") as fh:
        return pickle.load(fh)


def predict_event(symbol: str, event: EventRecord, as_of_date: str) -> float | None:
    """Predict p_up for a specific event at `as_of_date`. Returns None
    when the model isn't trained or the symbol has no bar for as_of_date.
    """
    bundle = load_model()
    if bundle is None:
        return None
    records = _load_records(symbol)
    date_idx = {r["date"]: i for i, r in enumerate(records)}
    idx = date_idx.get(as_of_date)
    if idx is None or idx + 1 < 220:
        return None
    snap = compute_snapshot(records[: idx + 1], symbol, as_of_date).model_dump()
    feats = _flatten_snapshot(snap)
    feats.update(_one_hot_event_type(event.event_type))
    feats["event_magnitude"] = float(event.magnitude)
    feats["event_direction_hint"] = float(event.direction_hint)
    feats["sector_20b_rs"] = _sector_20bar_return(symbol, as_of_date)
    feats["days_since_prev_same_event"] = 999.0  # sentinel; not used at inference
    x = np.array([[float(feats.get(c, 0.0)) for c in bundle["features"]]])
    return float(bundle["booster"].predict(x)[0])


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--no-broker-cluster", action="store_true",
                    help="Skip broker_cluster events (faster panel build)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    panel = build_panel(force=args.build,
                         include_broker_cluster=not args.no_broker_cluster)
    print()
    print(f"Panel: {len(panel)} event-anchored rows, "
          f"{panel['symbol'].nunique()} tickers")
    print(f"  event types: {panel['event_type'].value_counts().to_dict()}")
    print(f"  y=1 base rate: {panel['y'].mean():.3f}")

    if args.eval or not (args.build or args.train):
        report = walk_forward_eval(panel)
        print()
        print(f"=== Purged K-Fold eval ({report['n_folds']} folds, "
              f"{report['n_rows']} rows) ===")
        print(f"Mean hit rate ......... {report['mean_hit_rate']:.3f}")
        print(f"Top-decile threshold .. p >= {report['top_decile_threshold']:.3f}")
        print(f"Top-decile subset ..... n={report['top_decile_n']}, "
              f"hit_rate={report['top_decile_hit_rate']:.3f}")
        print()
        print("Per-event-type:")
        for t, s in report["per_event_type"].items():
            print(f"  {t:16s}  n={s['n']:>3d}  hit={s['hit_rate']:.3f}  brier={s['brier']:.3f}")
        print()
        print("Reliability curve (p bin -> actual):")
        for b in report["reliability"]:
            print(f"  [{b['bin_low']:.1f},{b['bin_high']:.1f})  "
                  f"n={b['n']:>3d}  mean_p={b['mean_p']:.3f}  "
                  f"actual={b['actual']:.3f}")

        rp = report.pop("raw_predictions")
        with open(WF_PRED_PATH, "wb") as fh:
            pickle.dump(rp, fh)
        print(f"\nRaw predictions -> {WF_PRED_PATH}")

    if args.train:
        train_final(panel)


if __name__ == "__main__":
    main()
