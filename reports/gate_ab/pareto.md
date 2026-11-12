# Cost--recall Pareto frontier (heuristic gate sweep)

Produced by `scripts/pareto_sweep.py`. The heuristic gate scores each decision from the journal's analyst signals + indicator snapshot using the LLM gate's trigger vocabulary, then thresholds at `tau` to simulate a more/less aggressive gate without spending new LLM tokens.

## Headline table

| Ticker | tau | proceed% | recall | tok. saved% | h=5 acc on proceed |
|---|---:|---:|---:|---:|---:|
| NVDA | 0.50 | 100% | 100.0% | 0.0% | 82.4% |
| TSLA | 0.50 | 100% | — | 0.0% | — |
| ORCL | 0.50 | 100% | 100.0% | 0.0% | 50.0% |
| KO | 0.50 | 100% | 100.0% | 0.0% | 81.8% |
| NVDA | 0.65 | 100% | 100.0% | 0.0% | 82.4% |
| TSLA | 0.65 | 100% | — | 0.0% | — |
| ORCL | 0.65 | 100% | 100.0% | 0.0% | 50.0% |
| KO | 0.65 | 100% | 100.0% | 0.0% | 81.8% |
| NVDA | 0.80 | 100% | 100.0% | 0.0% | 82.4% |
| TSLA | 0.80 | 62% | — | 34.6% | — |
| ORCL | 0.80 | 57% | 50.0% | 38.9% | 66.7% |
| KO | 0.80 | 58% | 54.5% | 36.6% | 100.0% |

Up-and-to-the-right is better: high recall with high token savings. See `pareto.png` for the full curves.
