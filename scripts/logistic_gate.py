"""Logistic-regression baseline for the pre-debate gate.

Research question: how much of the LLM gate's selection quality is
recoverable from a tiny linear model over indicator features already
in the journal? If logistic matches the heuristic / LLM gate's recall
curve, the LLM gate's value is interpretability and trigger-vocabulary,
not predictive accuracy. If logistic clearly loses, the LLM gate is
earning its ~1.5k tokens per call.

Setup:
  Target  y_t = 1 if baseline committee issued BUY or SELL at (ticker, t)
               0 if HOLD (the "not worth convening" counterfactual)
  Features (all from the journal; no LLM):
    - max_analyst_confidence        -- max of three analyst confidences
    - analyst_disagreement          -- pairwise min-conf of opposite directions
    - abs_net_bias                  -- |net_bias_score| from indicator snapshot
    - vol_regime_high               -- 1 if vol_regime == "high" else 0
    - vol_regime_mid                -- 1 if vol_regime == "mid" else 0
    - max_ringer                    -- max(bull_ringer, bear_ringer) / 3
    - pos_in_20d_range              -- (last_close - 20d_lo) / (20d_hi - 20d_lo)

Evaluation:
  Leave-one-ticker-out: fit on 3 tickers, predict on the held-out ticker.
  Report per-ticker ROC-AUC, PR-AUC; produce the logistic model's
  precision-recall sweep and overlay it with the heuristic gate's sweep
  (from pareto_sweep.py) so §6.3's "learned baseline" paragraph has a
  real comparator.

Outputs:
  reports/logistic/summary.json
  reports/logistic/summary.md
  reports/logistic/pr_curves.png
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env", override=True)

from scripts.eval_nasdaq import _load_ticker_records  # noqa: E402
from scripts.pareto_sweep import _score_triggers  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("logistic_gate")


FEATURE_NAMES = (
    "max_analyst_conf", "analyst_disagreement", "abs_net_bias",
    "vol_regime_high", "vol_regime_mid", "max_ringer", "pos_in_20d_range",
)


def _features(rec: dict) -> list[float]:
    """Build the feature vector for one journal record."""
    analyst_signals = rec.get("analyst_signals") or {}
    fund = analyst_signals.get("fundamentals") or {}
    sent = analyst_signals.get("sentiment") or {}
    tech = analyst_signals.get("technicals") or {}

    def _conf(s):
        try:
            return float((s or {}).get("confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _direction(s):
        sig = (s or {}).get("signal") or ""
        if sig == "bullish":
            return +1
        if sig == "bearish":
            return -1
        return 0

    confs = [_conf(fund), _conf(sent), _conf(tech)]
    max_conf = max(confs) if confs else 0.0

    # Analyst disagreement: min-conf among any pair on opposite sides
    dirs_confs = [(_direction(fund), _conf(fund)),
                  (_direction(sent), _conf(sent)),
                  (_direction(tech), _conf(tech))]
    disagree = 0.0
    for i in range(len(dirs_confs)):
        for j in range(i + 1, len(dirs_confs)):
            di, ci = dirs_confs[i]
            dj, cj = dirs_confs[j]
            if di != 0 and dj != 0 and di != dj:
                disagree = max(disagree, min(ci, cj))

    snap = (tech or {}).get("indicator_snapshot") or {}
    net_bias = snap.get("net_bias_score")
    try:
        abs_net_bias = abs(float(net_bias)) if net_bias is not None else 0.0
    except (TypeError, ValueError):
        abs_net_bias = 0.0

    vol_regime = ((snap.get("volatility") or {}).get("vol_regime") or "").lower()
    vol_high = 1.0 if vol_regime == "high" else 0.0
    vol_mid = 1.0 if vol_regime == "mid" else 0.0

    candles = snap.get("candles") or {}
    try:
        bull_r = float(candles.get("bull_ringer_score") or 0.0)
        bear_r = float(candles.get("bear_ringer_score") or 0.0)
    except (TypeError, ValueError):
        bull_r = bear_r = 0.0
    max_ringer = min(max(bull_r, bear_r) / 3.0, 1.0)

    # Position in 20d range from ohlcv_summary
    ohlcv_summary = rec.get("ohlcv_summary") or {}
    try:
        last_close = float(ohlcv_summary.get("last_close") or 0.0)
        hi_20 = float(ohlcv_summary.get("last_close_hi_20d") or last_close)
        lo_20 = float(ohlcv_summary.get("last_close_lo_20d") or last_close)
    except (TypeError, ValueError):
        last_close = hi_20 = lo_20 = 0.0
    if hi_20 > lo_20:
        pos_in_range = (last_close - lo_20) / (hi_20 - lo_20)
    else:
        pos_in_range = 0.5

    return [max_conf, disagree, abs_net_bias, vol_high, vol_mid,
            max_ringer, pos_in_range]


def _label(rec: dict) -> int:
    """1 if baseline committee issued directional, 0 if HOLD."""
    action = ((rec.get("final_decision") or {}).get("action") or "").lower()
    return 1 if action in ("buy", "sell", "long", "short") else 0


def _build_xy(journal_root: Path, ticker: str):
    """Return (X, y, heuristic_scores) as python lists for one ticker."""
    records = _load_ticker_records(journal_root, ticker)
    good = [r for r in records if not r.get("error")]
    X, y, h_scores = [], [], []
    for rec in good:
        X.append(_features(rec))
        y.append(_label(rec))
        score, _trig = _score_triggers(rec)
        h_scores.append(score)
    return X, y, h_scores


def _pr_curve(y_true: list[int], y_score: list[float]) -> list[dict]:
    """Precision/recall at each distinct score threshold."""
    thresholds = sorted(set(y_score), reverse=True)
    out = []
    for t in thresholds + [-1e-9]:
        tp = sum(1 for yt, s in zip(y_true, y_score) if s >= t and yt == 1)
        fp = sum(1 for yt, s in zip(y_true, y_score) if s >= t and yt == 0)
        fn = sum(1 for yt, s in zip(y_true, y_score) if s < t and yt == 1)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        out.append({"threshold": round(t, 4),
                    "precision": precision, "recall": recall})
    return out


def _auc(xs: list[float], ys: list[float]) -> Optional[float]:
    pts = sorted([(x, y) for x, y in zip(xs, ys)
                  if x is not None and y is not None])
    if len(pts) < 2:
        return None
    area = 0.0
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        area += (x1 - x0) * (y0 + y1) / 2.0
    return area


def _evaluate_loo(all_tickers: list[str], journal_root: Path) -> dict:
    """Leave-one-ticker-out logistic fit + per-ticker evaluation."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score, average_precision_score
    except ImportError as e:
        logger.error("Missing dependency: %s", e)
        return {}

    ticker_data = {t: _build_xy(journal_root, t) for t in all_tickers}

    results = {}
    for held_out in all_tickers:
        X_train, y_train = [], []
        for t in all_tickers:
            if t == held_out:
                continue
            X_t, y_t, _ = ticker_data[t]
            X_train.extend(X_t)
            y_train.extend(y_t)
        X_held, y_held, h_held = ticker_data[held_out]
        if not X_train or not X_held:
            results[held_out] = {"error": "empty split"}
            continue
        # Degenerate labels guard
        if len(set(y_train)) < 2:
            results[held_out] = {"error": "degenerate training labels",
                                 "note": "all held-in tickers are 0 or all 1"}
            continue

        X_train_a = np.array(X_train, dtype=float)
        y_train_a = np.array(y_train, dtype=int)
        X_held_a = np.array(X_held, dtype=float)
        y_held_a = np.array(y_held, dtype=int)

        scaler = StandardScaler().fit(X_train_a)
        X_train_s = scaler.transform(X_train_a)
        X_held_s = scaler.transform(X_held_a)

        model = LogisticRegression(max_iter=1000, C=1.0)
        model.fit(X_train_s, y_train_a)
        y_scores = model.predict_proba(X_held_s)[:, 1].tolist()

        # Metrics on held-out
        has_both = len(set(y_held_a.tolist())) == 2
        if has_both:
            roc = float(roc_auc_score(y_held_a, y_scores))
            pr = float(average_precision_score(y_held_a, y_scores))
        else:
            roc = pr = None

        # Heuristic-gate scores on the same held-out set
        h_roc = None; h_pr = None
        if has_both:
            try:
                h_roc = float(roc_auc_score(y_held_a, h_held))
                h_pr = float(average_precision_score(y_held_a, h_held))
            except Exception:
                h_roc = h_pr = None

        # PR curves (both)
        pr_logistic = _pr_curve(y_held_a.tolist(), y_scores)
        pr_heuristic = _pr_curve(y_held_a.tolist(), h_held)

        results[held_out] = {
            "n_train": len(y_train),
            "n_test": len(y_held),
            "n_positives_test": int(sum(y_held_a)),
            "logistic": {
                "roc_auc": roc, "pr_auc": pr,
                "coefficients": dict(zip(FEATURE_NAMES,
                                         model.coef_[0].tolist())),
                "intercept": float(model.intercept_[0]),
                "pr_curve": pr_logistic,
            },
            "heuristic_gate": {
                "roc_auc": h_roc, "pr_auc": h_pr,
                "pr_curve": pr_heuristic,
            },
        }
    return results


def _plot_pr(results: dict, out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    tickers = [t for t, r in results.items() if "error" not in r]
    n = len(tickers)
    if n == 0:
        logger.warning("No tickers to plot")
        return
    fig, axes = plt.subplots(1, n, figsize=(5 * n, 4), dpi=110, sharey=True)
    if n == 1:
        axes = [axes]
    for ax, t in zip(axes, tickers):
        r = results[t]
        for key, label, style in (("logistic", "logistic", "-"),
                                  ("heuristic_gate", "heuristic", "--")):
            curve = r[key]["pr_curve"]
            rs = [p["recall"] for p in curve if p["recall"] is not None
                  and p["precision"] is not None]
            ps = [p["precision"] for p in curve if p["recall"] is not None
                  and p["precision"] is not None]
            if rs:
                ax.plot(rs, ps, linestyle=style, label=label, linewidth=1.5)
        ax.set_xlabel("recall")
        if ax is axes[0]:
            ax.set_ylabel("precision")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.set_title(f"{t}\n(log PR-AUC={r['logistic']['pr_auc']}, "
                     f"heur={r['heuristic_gate']['pr_auc']})",
                     fontsize=9)
        ax.legend(loc="best", fontsize=9)
        ax.grid(True, alpha=0.3)
    fig.suptitle("Precision-recall: logistic vs heuristic gate (leave-one-ticker-out)",
                 fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    logger.info("Wrote %s", out_path)


def _render_markdown(results: dict) -> str:
    L = ["# Logistic-regression baseline for the gate", ""]
    L.append("Leave-one-ticker-out: fit on three tickers, test on the fourth. "
             "Target y=1 if baseline committee issued BUY/SELL, 0 if HOLD. "
             "Features: `max_analyst_conf`, `analyst_disagreement`, "
             "`abs_net_bias`, `vol_regime_high`, `vol_regime_mid`, "
             "`max_ringer`, `pos_in_20d_range`. The heuristic gate from "
             "`pareto_sweep.py` is compared on the same held-out split.")
    L.append("")
    L.append("## Per-ticker held-out metrics")
    L.append("")
    L.append("| Held-out | n test | positives | logistic ROC-AUC | log PR-AUC | heur ROC-AUC | heur PR-AUC |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for t, r in results.items():
        if "error" in r:
            L.append(f"| {t} | — | — | — | — | — | — | (skipped: {r['error']}) |")
            continue
        def _f(x): return "—" if x is None else f"{x:.3f}"
        L.append("| " + " | ".join([
            t, str(r["n_test"]), str(r["n_positives_test"]),
            _f(r["logistic"]["roc_auc"]), _f(r["logistic"]["pr_auc"]),
            _f(r["heuristic_gate"]["roc_auc"]),
            _f(r["heuristic_gate"]["pr_auc"]),
        ]) + " |")
    L.append("")
    L.append("## Logistic coefficients (held-in fit)")
    L.append("")
    L.append("| Held-out | " + " | ".join(FEATURE_NAMES) + " | intercept |")
    L.append("|---|" + "---:|" * (len(FEATURE_NAMES) + 1))
    for t, r in results.items():
        if "error" in r:
            continue
        coefs = r["logistic"]["coefficients"]
        row = [t] + [f"{coefs[f]:+.2f}" for f in FEATURE_NAMES]
        row.append(f"{r['logistic']['intercept']:+.2f}")
        L.append("| " + " | ".join(row) + " |")
    L.append("")
    L.append("Positive coefficient on a feature means higher-feature-value "
             "pushes the model toward *proceed*; negative pushes toward *skip*. "
             "Compare signs across tickers: if `analyst_disagreement` consistently "
             "attracts a positive weight, that trigger is doing real work. Mixed "
             "signs across LOO folds suggest the feature is ticker-specific "
             "rather than universal.")
    L.append("")
    return "\n".join(L)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Logistic baseline for the gate")
    p.add_argument("--baseline",
                   default=str(REPO_ROOT / "logs" / "decisions" / "nasdaq_focus"))
    p.add_argument("--universe",
                   default=str(REPO_ROOT / "data" / "universe" / "nasdaq_focus.json"))
    p.add_argument("--tickers", default=None)
    p.add_argument("--out", default=str(REPO_ROOT / "reports" / "logistic"))
    return p.parse_args()


def main():
    args = _parse_args()
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = json.loads(Path(args.universe).read_text())["tickers"]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    results = _evaluate_loo(tickers, Path(args.baseline))

    for t, r in results.items():
        if "error" in r:
            logger.warning("%s: %s", t, r["error"])
            continue
        log = r["logistic"]; heu = r["heuristic_gate"]
        logger.info("%s  n_test=%d  pos=%d  logistic ROC=%s PR=%s  heuristic ROC=%s PR=%s",
                    t, r["n_test"], r["n_positives_test"],
                    log["roc_auc"], log["pr_auc"],
                    heu["roc_auc"], heu["pr_auc"])

    (out_root / "summary.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    (out_root / "summary.md").write_text(
        _render_markdown(results), encoding="utf-8")
    _plot_pr(results, out_root / "pr_curves.png")
    logger.info("Wrote %s/{summary.json, summary.md, pr_curves.png}", out_root)


if __name__ == "__main__":
    main()
