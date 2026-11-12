# NASDAQ focus backtest — evaluation summary

Tickers: NVDA, TSLA, ORCL, KO   Universe: `data/universe/nasdaq_focus.json`

## Directional accuracy — final decision
| Ticker | h=1 | h=5 | h=20 | n_records | n_errors |
|---|---:|---:|---:|---:|---:|
| NVDA | +52.94% | +82.35% | +70.59% | 53 | 0 |
| TSLA | — | — | — | 53 | 0 |
| ORCL | +66.67% | +50.00% | +50.00% | 53 | 0 |
| KO | +54.55% | +81.82% | +63.64% | 53 | 0 |

## Directional accuracy — per analyst (h=5)
| Ticker | fundamentals | sentiment | technicals | judge | bull | bear |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| NVDA | +62.26% | +63.64% | +45.24% | +62.26% | +62.26% | +37.74% |
| TSLA | +0.00% | +50.00% | +46.51% | +49.06% | +43.40% | +56.60% |
| ORCL | +87.50% | +100.00% | +48.94% | +56.60% | +50.94% | +49.06% |
| KO | +100.00% | — | +53.66% | +62.26% | +49.06% | +50.94% |

## Risk-adjusted returns
| Ticker | Sharpe (ann) | Sortino | Max DD | Calmar |
|---|---:|---:|---:|---:|
| NVDA | +1.58 | +2.29 | -0.73% | +2.71 |
| TSLA | — | — | +0.00% | — |
| ORCL | +1.23 | +3.00 | -0.01% | +2.30 |
| KO | +0.85 | +1.02 | -0.27% | +0.99 |

## Debate-symmetry sanity check
| Ticker | n | mean bull_conf | mean bear_conf | abs gap |
|---|---:|---:|---:|---:|
| NVDA | 53 | 0.679 | 0.419 | 0.260 |
| TSLA | 53 | 0.452 | 0.562 | 0.110 |
| ORCL | 53 | 0.475 | 0.528 | 0.054 |
| KO | 53 | 0.478 | 0.491 | 0.012 |

A large persistent `abs_gap` under the OpenAI-symmetric router would mean the debate is model-biased even without provider asymmetry.
