# Non-LLM baselines vs Argos

Buy-and-hold is the null hypothesis every claim in §6 must beat. MA-cross is a published zero-cost comparator. Random is sampled at the EMPIRICAL (buy, sell) rate observed in Argos's own baseline journals per ticker, 1000 draws. Sharpe CIs are 95% bootstrap (1000 resamples with replacement of the per-period return series).

## Headline table

| Ticker | Strategy | Return% | Sharpe | 95% CI | n periods |
|---|---|---:|---:|---:|---:|
| NVDA | buy_and_hold | +178.87% | +1.01 | — | 251 |
| NVDA | ma_cross_20_50 | +39.87% | +0.48 | — | 251 |
| NVDA | random (emp.rate) | +74.22% | +1.59 | [+0.14, +2.97] | 1000 |
| NVDA | **argos (ours)** | +2.00% | **+1.68** | [-0.30, +3.83] | 53 |
| | | | | | |
| TSLA | buy_and_hold | +62.56% | +0.49 | — | 251 |
| TSLA | ma_cross_20_50 | +105.77% | +0.77 | — | 251 |
| TSLA | random (emp.rate) | — | — | — | 0 |
| TSLA | **argos (ours)** | +0.00% | — | — | 53 |
| | | | | | |
| ORCL | buy_and_hold | +62.10% | +0.76 | — | 251 |
| ORCL | ma_cross_20_50 | +23.74% | +0.42 | — | 251 |
| ORCL | random (emp.rate) | +29.23% | +1.15 | [-0.32, +2.35] | 1000 |
| ORCL | **argos (ours)** | +0.03% | **+1.18** | [-0.55, +2.81] | 53 |
| | | | | | |
| KO | buy_and_hold | +7.26% | +0.28 | — | 251 |
| KO | ma_cross_20_50 | +10.69% | +0.52 | — | 251 |
| KO | random (emp.rate) | +3.31% | +0.39 | [-1.18, +1.96] | 1000 |
| KO | **argos (ours)** | +0.27% | **+0.89** | [-1.19, +3.11] | 53 |
| | | | | | |

Reading this table: Argos must beat buy-and-hold AND its 95% CI must sit above the random policy's CI to claim meaningful alpha. If the random-policy CI contains Argos's Sharpe, the paper reports no distinguishable alpha at this sample size.
