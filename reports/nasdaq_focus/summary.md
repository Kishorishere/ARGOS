# NASDAQ focus backtest — evaluation summary

Tickers: NVDA, TSLA, ORCL   Universe: `data/universe/nasdaq_focus.json`

## Directional accuracy — final decision
| Ticker | h=1 | h=5 | h=20 | n_records | n_errors |
|---|---:|---:|---:|---:|---:|
| NVDA | +57.14% | +75.00% | +75.00% | 97 | 0 |
| TSLA | +25.00% | +0.00% | +25.00% | 53 | 0 |
| ORCL | +44.44% | +66.67% | +66.67% | 53 | 0 |

## Directional accuracy — per analyst (h=5)
| Ticker | fundamentals | sentiment | technicals | judge | bull | bear |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| NVDA | +75.79% | +75.34% | +44.29% | — | +75.79% | +24.21% |
| TSLA | +0.00% | +50.00% | +42.42% | — | +43.40% | +56.60% |
| ORCL | +87.50% | +100.00% | +52.63% | — | +50.94% | +49.06% |

## Risk-adjusted returns
| Ticker | Sharpe (ann) | Sortino | Max DD | Calmar |
|---|---:|---:|---:|---:|
| NVDA | — | — | — | — |
| TSLA | — | — | — | — |
| ORCL | — | — | — | — |

## Debate-symmetry sanity check
| Ticker | n | mean bull_conf | mean bear_conf | abs gap |
|---|---:|---:|---:|---:|
| NVDA | 97 | 0.715 | 0.390 | 0.326 |
| TSLA | 53 | 0.496 | 0.494 | 0.002 |
| ORCL | 53 | 0.508 | 0.477 | 0.031 |

A large persistent `abs_gap` under the OpenAI-symmetric router would mean the debate is model-biased even without provider asymmetry.
