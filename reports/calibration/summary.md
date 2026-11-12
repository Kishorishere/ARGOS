# Per-agent calibration (h=5)

Produced by `scripts/calibration.py` over the baseline journals. Each row is one (ticker, agent) pair: Brier score (lower is better; 0.25 = random), ECE (expected calibration error; lower is better), mean confidence, mean realized accuracy on directional calls, and sample size.

| Ticker | Agent | n | Brier | ECE | mean conf | mean acc | gap |
|---|---|---:|---:|---:|---:|---:|---:|
| NVDA | fundamentals | 53 | 0.274 | 0.208 | 0.818 | 0.623 | +0.196 |
| NVDA | sentiment | 44 | 0.258 | 0.165 | 0.801 | 0.636 | +0.165 |
| NVDA | technicals | 42 | 0.253 | 0.055 | 0.507 | 0.452 | +0.055 |
| NVDA | judge | 53 | 0.266 | 0.184 | 0.771 | 0.623 | +0.148 |
| NVDA | bull | 53 | 0.229 | 0.057 | 0.679 | 0.623 | +0.057 |
| NVDA | bear | 53 | 0.239 | 0.042 | 0.419 | 0.377 | +0.042 |
| TSLA | fundamentals | 4 | 0.640 | 0.800 | 0.800 | 0.000 | +0.800 |
| TSLA | sentiment | 8 | 0.334 | 0.262 | 0.762 | 0.500 | +0.262 |
| TSLA | technicals | 43 | 0.250 | 0.035 | 0.500 | 0.465 | +0.035 |
| TSLA | judge | 53 | 0.334 | 0.268 | 0.758 | 0.491 | +0.268 |
| TSLA | bull | 53 | 0.273 | 0.222 | 0.452 | 0.434 | +0.018 |
| TSLA | bear | 53 | 0.289 | 0.219 | 0.562 | 0.566 | -0.004 |
| ORCL | fundamentals | 8 | 0.115 | 0.075 | 0.800 | 0.875 | -0.075 |
| ORCL | sentiment | 2 | 0.040 | 0.200 | 0.800 | 1.000 | -0.200 |
| ORCL | technicals | 47 | 0.254 | 0.028 | 0.513 | 0.489 | +0.023 |
| ORCL | judge | 53 | 0.278 | 0.200 | 0.766 | 0.566 | +0.200 |
| ORCL | bull | 53 | 0.238 | 0.073 | 0.475 | 0.509 | -0.035 |
| ORCL | bear | 53 | 0.249 | 0.068 | 0.528 | 0.491 | +0.038 |
| KO | fundamentals | 1 | 0.040 | 0.200 | 0.800 | 1.000 | -0.200 |
| KO | sentiment | 0 | — | — | — | — | — |
| KO | technicals | 41 | 0.257 | 0.041 | 0.549 | 0.537 | +0.012 |
| KO | judge | 53 | 0.246 | 0.135 | 0.758 | 0.623 | +0.135 |
| KO | bull | 53 | 0.249 | 0.095 | 0.478 | 0.491 | -0.012 |
| KO | bear | 53 | 0.243 | 0.072 | 0.491 | 0.509 | -0.019 |

`gap = mean_confidence - mean_accuracy`. Positive = overconfident; negative = underconfident; near zero = well-calibrated on average.
