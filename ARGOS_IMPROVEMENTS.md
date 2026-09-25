# Argos — 50+ Concrete Advancements Roadmap

> Snapshot of everything worth building next, ordered by expected **edge-per-hour**. Each item has a **why** (what breaks or leaks without it), a **how** (concrete implementation hook), and a **cost** (S/M/L: hours of dev).

---

## Section A — Data & Coverage (foundational; blocks everything else)

**A1. NEPSE historical loader hardening (S)** — cache Merolagani responses to disk (Parquet under `data/cache/nepse/{symbol}.parquet`), incremental append, TTL. **Why:** cuts backtest re-runs from minutes to seconds, avoids rate-limits. **Hook:** `argos/data/nepse_ohlcv.py::load_nepse_ohlcv`.

**A2. NEPSE intraday snapshot poller (M)** — every N minutes call `nepse_api.fetch_market_snapshot()` and append to `data/live/nepse_snapshots.jsonl`. Build 30d rolling history of gainers/losers/breadth. **Why:** enables sentiment/regime signals with real intraday flow. **Hook:** new `scripts/poll_nepse_live.py` + Windows Task Scheduler.

**A3. Corporate actions loader (M)** — dividends, bonuses, rights, splits from ShareSansar. Adjust OHLCV history for the split factor. **Why:** unadjusted prices silently corrupt every indicator across a split. **Hook:** new `argos/data/nepse_corporate_actions.py`.

**A4. Floorsheet ingestion (L)** — broker-level trade flow from Merolagani/ShareSansar. Broker-concentration and "smart-money" signals are alpha on NEPSE specifically. **Hook:** new `argos/data/nepse_floorsheet.py`; the terminal already has a scraper worth copying.

**A5. Sector + index history (S)** — the snapshot API returns today's `summary.nepseIndex`; snapshot daily and build a sector-index OHLCV series. **Why:** enables cross-sectional (relative-strength) signals — "long stocks strongest vs their sector index". **Hook:** extends A2.

**A6. Fundamentals + quarterly earnings loader (M)** — ShareSansar quarterly reports scraper (terminal already has one). EPS, revenue growth, P/E, book value — feed into fundamentals agent. **Hook:** new `argos/data/nepse_fundamentals.py`.

**A7. News & sentiment loader (M)** — ShareSansar news + Merolagani news + optional Twitter/X search. Feed into sentiment agent. **Why:** sentiment currently comes only from Gemini's grounded web search which misses Nepali sources. **Hook:** new `argos/data/nepse_news.py`.

**A8. Macro overlay (S)** — remittance data, forex NPR/USD, oil prices, gold, Indian NIFTY. Feed to Risk Manager as a regime context. **Hook:** new `argos/data/macro_context.py`.

**A9. Rainfall + hydropower signal (M)** — the terminal has this for hydropower stocks; useful for HDL, UPPER, etc. **Hook:** new `argos/data/hydro_context.py`.

**A10. Multi-timeframe OHLCV (M)** — pull 1H / 4H bars via Merolagani (or synthesize from daily on-close). Compute IndicatorSnapshot per timeframe so the technicals agent sees daily + weekly context. **Why:** MACD on 4H catches turns before daily; daily-only misses intraday regime shifts. **Hook:** extend `argos/indicators/engine.py::compute_snapshot(records, timeframe=...)`.

---

## Section B — Indicator engine deepening

**B11. VWAP anchored to session / event (S)** — anchored VWAP from swing lows and earnings dates. **Why:** standard institutional level; often acts as support/resistance. **Hook:** extend `argos/indicators/engine.py::_vwap`.

**B12. Volume profile & POC (M)** — build a 30/50-day volume-by-price histogram, flag value area high/low + point-of-control. **Hook:** new function in `engine.py`.

**B13. Ichimoku cloud (S)** — tenkan / kijun / senkou A+B / chikou. Adds a distinct trend + momentum layer. **Hook:** `_ichimoku(...)` in `engine.py`.

**B14. Fibonacci retracement + extension levels (S)** — from the last swing high/low pair; nearest 38.2/50/61.8 as S/R. **Hook:** extends `_build_structure`.

**B15. Elder's Force Index / TRIX / Chaikin oscillator (S)** — cheap adds; more volume-adjusted momentum signals. **Hook:** `engine.py`.

**B16. Squeeze quality score (S)** — how long has BB<Keltner been true? Length-of-squeeze is more predictive than the current binary flag. **Hook:** track `bb_less_than_keltner` for the last 20 bars.

**B17. Higher-timeframe regime overlay (S)** — is the daily EMA200 slope up or down? Bull rules only fire when higher-TF regime is bullish. **Hook:** `snap.trend.higher_tf_regime`.

**B18. Signal-decay tracking (S)** — each fired signal gets an `age_bars` (how many bars since it first fired). Fresh signals get more weight. **Hook:** requires persisting the last-fired frame per indicator (use `data/cache/signal_state.json`).

**B19. Adaptive thresholds (M)** — RSI oversold isn't always <30; on high-vol regimes shift to <25, on low-vol <35. Fit per-symbol percentiles offline. **Hook:** `argos/indicators/thresholds.py` with per-symbol config JSON.

**B20. Multi-oscillator agreement score (S)** — how many oscillators (RSI, Stoch, StochRSI, MFI, Williams) agree on overbought/oversold? Agreement > 3 is a much stronger signal than any one alone. **Hook:** `snap.momentum.agreement_score`.

**B21. Pattern-based S/R clustering (M)** — cluster swing highs/lows within 0.5×ATR to find real levels vs. one-touch noise. **Hook:** extends `_build_structure`.

**B22. Divergence quality score (M)** — currently binary; upgrade to (a) how many bars between the two pivots (b) how steep the divergence is (c) whether it's on a support level. **Hook:** rework `patterns.detect_divergences`.

**B23. Gap detection (S)** — opening gaps > 1×ATR, classify as breakaway / exhaustion / common. **Hook:** new `_build_gaps` in `engine.py`.

**B24. Money-flow anomaly (S)** — MFI + volume-z + CMF all diverge from price → whale accumulation/distribution flag. **Hook:** combine in `_build_volume`.

---

## Section C — Debate & LLM stack quality

**C25. Multi-round debate that actually converges (M)** — currently rounds > 1 just tack on more turns; add a "moderator" node that scores each turn and terminates early when confidence deltas fall below ε. **Hook:** new `argos/nodes/debate_moderator.py` + edge in `graph.py`.

**C26. Judge / referee node (M)** — after N rounds, an independent LLM (different model or same model with fresh context) reads only the transcript and returns a final winner + confidence. Fed to portfolio manager as a prior. **Hook:** new node between debate and risk.

**C27. Structured critique of upstream agents (S)** — Risk Manager currently sees the debate; add a "critique" field where it flags weak-evidence claims by name. This gets journaled and used to trim future prompt bloat. **Hook:** extend `RiskVerdict` schema.

**C28. Confidence calibration (M)** — over time, compare each agent's stated confidence to realized outcomes and compute a calibration curve. Recalibrate prompts. **Hook:** `argos/journal.py` already stores everything; new `scripts/calibrate_agents.py`.

**C29. Chain-of-verification pass (M)** — after fundamentals produces its numbers, run a second call asking "list every specific claim that could be false; find it via search". Removes hallucinated EPS/PE numbers. **Hook:** wrap `fundamentals_node`.

**C30. Prompt-caching (S)** — Gemini supports prompt caching (system prompts + IndicatorSnapshot preamble). Cuts per-call cost by ~40%. **Hook:** `argos/model_router.py::_make_llm` cache config.

**C31. Retry-with-repair for schema failures (S)** — when `.with_structured_output()` fails to parse, retry with the error text appended, not just retry blind. **Hook:** wrap `structured_llm.invoke`.

**C32. Rate-limit budget per date (S)** — track Gemini tokens/day; if a date would exceed a budget, defer to next run. **Hook:** `argos/model_router.py` + `data/state/gemini_budget.json`.

**C33. Local LLM fallback for narration (M)** — Ollama Qwen/Llama for prompts that don't need frontier reasoning (e.g., "summarize this indicator snapshot"). Cuts Gemini quota by ~30%. **Hook:** new `argos/model_router.py::get_llm(agent, prefer_local=True)`.

**C34. Deterministic seeding + temperature audit (S)** — record temperature + seed per LLM call in the journal; enables re-run reproducibility. **Hook:** journal.

**C35. Multi-model ensemble for portfolio manager (M)** — run the final decision through 3 LLMs (Gemini flash, Gemini pro, one OpenRouter free), take the majority. Only for the final synthesis, not analysts. **Hook:** `portfolio_manager_node`.

---

## Section D — Sizing, risk, portfolio

**D36. Kelly-fraction position sizing (M)** — size each entry by (expected edge × confidence) / variance, capped by risk-manager's `max_position_size`. **Why:** current 50%-cash flat sizing wastes the extra edge at high-conviction |bias|≥3 setups where hit rate rises. **Hook:** new `argos/portfolio/sizing.py`.

**D37. ATR-based stops (S)** — every buy sets a stop at `entry - 2×ATR(14)`. Journal it; the simulator obeys stops on next bar. **Hook:** `argos/simulate.py`.

**D38. Trailing stops (S)** — Chandelier exit: stop = `swing_high - 3×ATR`, walks up with price. **Hook:** `argos/simulate.py`.

**D39. Regime-conditional entry (M)** — do not open longs when `trend.regime == "downtrend"` unless there is a strong divergence + support setup. Cuts NABIL/GBIME-style losers seen in the NEPSE backtest. **Hook:** portfolio_manager prompt + hard filter.

**D40. Sector concentration cap (S)** — enforce per-sector weight cap in the Risk Manager. Currently `sector_weights_pct` is computed but no enforcement. **Hook:** `risk_manager_node`.

**D41. Correlation-aware sizing (M)** — track pairwise 60d correlation between held names; scale down new entries with correlation > 0.7 to existing holdings. **Hook:** `argos/portfolio/state.py` + `sizing.py`.

**D42. Drawdown circuit-breaker (S)** — if paper NAV drops > X% from peak, force all decisions to HOLD until recovery of Y%. **Hook:** `run_backtest.py` / live driver.

**D43. Cash-drag scheduling (S)** — when portfolio is 100% cash for > 5 days, force one dip-buy trial trade to test whether the strategy is stuck or the market is chopping. Track separately. **Hook:** state field.

**D44. NEPSE-specific circuit-breaker awareness (S)** — NEPSE has ±10% daily price limits and trading halts. Encode into the Risk Manager prompt + refuse to size entries against a symbol that hit circuit that day. **Hook:** risk prompt + `nepse_api.market_breadth`.

---

## Section E — Validation & backtesting rigor

**E45. Walk-forward with rolling refit (M)** — currently the indicator thresholds are static; add a rolling-window refit of the composite weights every 90 days. **Hook:** `argos/indicators/weights.py` + backtest driver.

**E46. Purged K-fold cross-validation (M)** — López de Prado's technique — hold-out folds with a purge window to prevent leakage from overlapping labels. **Hook:** new `argos/validation/purged_kfold.py`.

**E47. Combinatorial CSCV / PBO (L)** — Probability of Backtest Overfitting. Terminal already has this — port. **Hook:** `argos/validation/cscv.py`.

**E48. Deflated Sharpe ratio (M)** — corrects Sharpe for the number of strategy variants tried. **Hook:** `argos/validation/deflated_sharpe.py`.

**E49. Random-baseline benchmark (S)** — same backtest with `action = random.choice(['buy','hold','sell'])`; if we don't beat that at p < 0.05, we don't have edge. **Hook:** `argos/simulate.py` flag.

**E50. Bootstrap confidence intervals on hit rate (S)** — a 55% hit rate on n=800 has a ~4% 95% CI. Report both. **Hook:** `argos/journal.py` post-run summary.

**E51. Per-regime performance breakdown (S)** — split hit-rate + P&L by (trend regime × vol regime). We already tag every snapshot with regime. **Hook:** simulator report.

**E52. Trade-level attribution (M)** — for each winning/losing trade, which indicators fired at entry? Which agent had highest confidence? Enables killing bad indicators. **Hook:** journal already has it; new `scripts/attribute_trades.py`.

**E53. Live-vs-backtest drift monitor (M)** — every day compare the indicator distribution to the training distribution; alert when drift z > 3. **Hook:** new `scripts/drift_monitor.py`.

---

## Section F — Observability, ops, and UX

**F54. Grafana / Streamlit dashboard on the journal (M)** — hit rate over time, per-agent latency, indicator-fire histogram. Nothing exists today except JSONL. **Hook:** new `dashboards/streamlit_app.py`.

**F55. Telegram / Slack alerting (S)** — final_decision + rationale posted to a channel per date. **Hook:** new `argos/notify.py`.

**F56. `/argos-explain` MCP tool (M)** — expose "why did you decide X on date Y" as an MCP tool, so the future terminal integration can query the journal via natural language. **Hook:** new `argos/mcp/server.py`.

**F57. Version-tagged decisions (S)** — every journal record already has `argos_version`. Add a `strategy_version` field bumped when composite weights change. Enables clean A/B analysis. **Hook:** journal.

**F58. Cost tracker (S)** — sum Gemini tokens + Kronos-inference count per date. Weekly cost/decision. **Hook:** journal.

**F59. Deterministic replay tests (S)** — freeze one journal record + mock LLM; assert running the same date reproduces the same signal. **Hook:** new `tests/test_replay.py`.

**F60. CI on real historical run (S)** — GitHub Actions job that runs a mocked-LLM 5-date backtest on every push, asserts no regression in indicator counts / signals fired. **Hook:** `.github/workflows/argos_ci.yml`.

---

## Section G — Terminal integration (deferred phase)

**G61. `journal_to_signal_envelope` adapter (S)** — mechanical mapping (per PROJECT_COMPARISON.md's table). One file. **Hook:** new `argos/adapters/nqt.py`.

**G62. MCP client to NQT (M)** — call `run_multi_agent_decision`, `submit_paper_order` via FastMCP. **Hook:** `argos/adapters/nqt_mcp.py`.

**G63. Shared decision journal (M)** — NQT reads Argos journal directly for its own UI. Both processes agree on the same JSONL schema. **Hook:** schema doc.

**G64. Reconciliation reports (S)** — nightly compare Argos-suggested decisions vs. NQT's realized paper fills. Slippage attribution. **Hook:** cron.

---

## Section H — Speculative / research bets

**H65. Fine-tune Kronos on NEPSE data (L)** — pretrained on global equities; adapt to NEPSE's 3-year tick-frequency. Should improve `predicted_move_pct` accuracy meaningfully. **Cost:** Colab time.

**H66. Distillation of Gemini debate into a small classifier (L)** — after 500 dates, train an XGBoost on `[indicator_snapshot, macro] → gemini_final_action`. Get sub-100ms decisions. Use Gemini only for weekly recalibration. **Hook:** new `argos/ml/distill.py`.

**H67. Reinforcement learning from journal outcomes (L)** — treat each date as an (obs, action, reward) tuple; PPO on the composite weights. Requires many months of journal data first.

**H68. Multi-agent debate over multiple candidate trades per day (L)** — instead of one-ticker-per-run, feed all 341 NEPSE names, let agents pick top-K. Requires screening + ranking layer. **Hook:** new `argos/screener/`.

**H69. Options / futures overlay (L)** — if any NEPSE derivatives exist, use the technical bias for delta-hedged strategies. Longer horizon.

**H70. LLM-as-a-judge continual improvement (M)** — weekly, an LLM reads last week's decisions + realized outcomes and writes a critique that gets appended to each agent's system prompt (versioned). Bootstrap loop.

---

## Priority order (best edge-per-hour)

**Do first (this week):** A1 (cache), A3 (splits!), D36 (Kelly), D37 (stops), D39 (regime filter), D40 (sector caps), E49 (random baseline), E50 (CIs on hit rate), E51 (per-regime split), F55 (Telegram alerts).

**Second wave:** A2 (live poller), A6 (fundamentals), A7 (news), B13 (Ichimoku), B17 (HTF regime), B22 (divergence quality), C25 (moderator), C29 (verification), D38 (trailing), E45 (rolling refit).

**Third wave:** the deeper research + integration items (H65-H70, G61-G64, L-tagged).
