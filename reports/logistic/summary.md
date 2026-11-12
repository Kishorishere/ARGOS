# Logistic-regression baseline for the gate

Leave-one-ticker-out: fit on three tickers, test on the fourth. Target y=1 if baseline committee issued BUY/SELL, 0 if HOLD. Features: `max_analyst_conf`, `analyst_disagreement`, `abs_net_bias`, `vol_regime_high`, `vol_regime_mid`, `max_ringer`, `pos_in_20d_range`. The heuristic gate from `pareto_sweep.py` is compared on the same held-out split.

## Per-ticker held-out metrics

| Held-out | n test | positives | logistic ROC-AUC | log PR-AUC | heur ROC-AUC | heur PR-AUC |
|---|---:|---:|---:|---:|---:|---:|
| NVDA | 53 | 17 | 0.438 | 0.292 | 0.656 | 0.458 |
| TSLA | 53 | 0 | — | — | — | — |
| ORCL | 53 | 6 | 0.390 | 0.171 | 0.466 | 0.102 |
| KO | 53 | 11 | 0.574 | 0.369 | 0.453 | 0.190 |

## Logistic coefficients (held-in fit)

| Held-out | max_analyst_conf | analyst_disagreement | abs_net_bias | vol_regime_high | vol_regime_mid | max_ringer | pos_in_20d_range | intercept |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NVDA | +0.49 | -0.32 | +0.10 | -0.41 | +0.13 | +0.08 | +0.00 | -2.24 |
| TSLA | +0.63 | -0.34 | +0.26 | +0.24 | +0.20 | -0.18 | +0.00 | -1.38 |
| ORCL | +0.73 | -0.31 | +0.31 | +0.60 | +0.52 | -0.17 | +0.00 | -1.73 |
| KO | +0.99 | -0.25 | +0.37 | +0.28 | +0.07 | -0.21 | +0.00 | -2.04 |

Positive coefficient on a feature means higher-feature-value pushes the model toward *proceed*; negative pushes toward *skip*. Compare signs across tickers: if `analyst_disagreement` consistently attracts a positive weight, that trigger is doing real work. Mixed signs across LOO folds suggest the feature is ticker-specific rather than universal.
