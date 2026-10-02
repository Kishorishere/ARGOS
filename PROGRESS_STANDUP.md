# Argos — Progress Standup

**Reporting window:** last ~10 days (from the 7-agent-architecture
checkpoint through today, 2026-09-27).

**Checkpoint we're measuring from:** a bare seven-agent LangGraph
skeleton — Fundamentals / Sentiment / Technicals / Bull / Bear / Risk /
Portfolio Manager, plumbed through `graph.py`, a shared `FuseState`, a
`model_router`, and a decision `journal`. Nothing else. No data, no
indicators, no risk math, no backtester, no reports.

**Where we are now:** end-to-end research desk with real NEPSE data,
real indicators, real risk & sizing, a walk-forward backtester, agent
prompts, a signal ledger, statistical validation, reports, and the
fellowship-fork/private-repo split infrastructure.

Below, grouped by layer, is what changed and why.

---

## 1. Agent orchestration layer (the "seven agents" itself)

Started as: seven agents wired serially through a LangGraph, no
persona prompts, no debate structure beyond one Bull turn and one Bear
turn.

**Added since:**
- **Bull/Bear debate loop.** Multi-turn debate with configurable rounds,
  live in `argos/nodes/debate.py`. Both sides now consume specialist
  outputs and each other's prior turn — not a single-shot exchange.
- **Judge node.** A neutral referee that reads the debate transcript
  blind (side labels stripped) and returns a winner + confidence. Used
  as a tiebreaker by the Portfolio Manager. Lives in
  `argos/nodes/judge.py`.
- **Exit Manager (new 8th agent, effectively).** Every `FinalDecision`
  now carries `stop_price`, `target_price`, `time_stop_bars`, and a
  trailing-stop policy. `argos/nodes/exit_manager.py` produces these;
  the backtester honors them. This is the change that turns "one-shot
  directional signal" into a real position with exit logic.
- **Forecast node.** Wraps the Kronos client so technicals sees a
  calibrated direction score, not raw magnitude — see §3.
- **Persona prompt files.** `prompts/` now has a dedicated markdown
  file per persona: bull_advocate, bear_advocate, technicals_analyst,
  fundamentals_analyst, sentiment_analyst, risk_manager,
  portfolio_manager, exit_manager. No more mega-prompt with
  role-switching.

## 2. NEPSE data pipeline (was: nothing)

Entire `argos/data/` package is new. Real Nepal Stock Exchange
adapters, not toy fixtures:

- `nepse_ohlcv.py`, `nepse_api.py`, `ohlcv_loader.py` — historical and
  intraday price data, cached to `data/cache/nepse/*.parquet`
- `nepse_sectors.py` — sector-index history for relative-strength signals
- `floorsheet.py` + `broker_patterns.py` + `broker_reputation.py` —
  broker-level trade flow ("smart-money" concentration signals) that
  are alpha specifically on NEPSE
- `flow_analytics.py`, `beta_adjusted.py` — flow-derived features and
  beta-adjusted returns
- `nepali_sentiment.py` — sentiment feed for local news sources that
  Gemini's grounded search misses
- `macro_context.py` — remittance, forex NPR/USD, gold, NIFTY overlays
  fed to the Risk Manager as regime context
- `corporate_actions.py` + `announcements.py` +
  `announcement_reaction.py` — dividend/bonus/rights/split adjustments
  and announcement-reaction features
- `kronos_client.py` — client for the Kronos forecast model, hosted on
  a Colab notebook (`Argos_Kronos_Host.ipynb`, `Kronos_Colab_Server.ipynb`)

## 3. Indicator engine (was: nothing)

Entire `argos/indicators/` package is new:

- `engine.py` — TA engine producing an `IndicatorSnapshot` per symbol
- `patterns.py` — chart patterns and divergence detection
- `hurst.py` — Hurst-exponent regime detection
- `multi_timeframe.py` — daily + weekly + 4H snapshots so the
  technicals agent sees higher-timeframe context, not just daily
- `weights.py` — adaptive per-symbol weights for combining indicator
  signals into a net bias
- `freshness.py` — signal-age gating so stale signals decay in weight
- `schemas.py` — typed indicator output surfaces

## 4. Risk stack (was: nothing)

Entire `argos/risk/` package is new:

- `garch.py` — GARCH(1,1) forward volatility estimates
- `drawdown.py` — realized drawdown tracking
- `correlation.py` — pairwise & rolling correlations for portfolio
  concentration checks
- `vol_target.py` — volatility-targeted position size ceiling

## 5. Sizing, screening, fundamentals, portfolio (was: nothing)

- `argos/portfolio/` — Kelly-fractional sizing tied to composite
  conviction (net-bias × Kronos-alignment × agent-agreement × risk-cap).
  The Portfolio Manager now calls the sizing function instead of
  hand-picking a fraction.
- `argos/screener/` — sector-relative-strength ranking to feed the
  universe.
- `argos/fundamentals/` — fundamentals scoring + store, backed by a
  Gemini fundamentals fetcher (`scripts/fetch_fundamentals_gemini.py`).

## 6. Backtester, reports, dashboard (was: nothing)

- `argos/simulate.py` — walk-forward backtest engine that honors the
  Exit Manager's stops/targets/time-stops.
- `argos/reports/` — daily, weekly, monthly, and dashboard report
  generators (Markdown + charts) so runs are legible after the fact.
- `run_backtest.py` — top-level entrypoint.
- `scripts/run_screener_backtest.py`, `scripts/nepse_dashboard.py`,
  `scripts/daily_ingest.py`, `scripts/update_signal_ledger.py` —
  operational scripts for daily use.
- `scripts/one_week_forecast.py` — new (currently untracked) one-week
  forward Kronos forecast helper.

## 7. Feedback / learning layer

- `argos/learn/signal_ledger.py` — online-learning ledger: each fired
  signal is journaled with its context; realized outcomes feed back
  into per-symbol / per-indicator weights.
- `argos/ensemble/` — multi-source aggregation of LLM outputs so the
  Bull and Bear runs can be ensembled across providers to cancel
  per-model bias (see the 44-date ADBL finding below).
- `argos/validation/` — deflated Sharpe and purged k-fold, so
  backtest results survive the multiple-testing critique.

## 8. Tests

`tests/` grew from empty to full coverage of the agent layer plus
end-to-end:

- `test_nodes.py`, `test_debate_loop.py`, `test_graph.py`,
  `test_journal.py` — agent slice
- `test_indicators.py`, `test_flow_and_broker_signals.py` — data +
  indicator slice
- `test_e2e.py` — full pipeline against fixture data

## 9. Research & writing (private-repo only)

- `main.tex` + `literature_review.tex` + `references.bib` — the paper
  draft
- `Argos_Proposal_Deck.pptx` + `argos_deck_gen.py` — the fellowship
  proposal deck and its generator
- `plan.html` — internal roadmap

## 10. Architecture & planning docs

- `ARGOS_BUILD_SPEC.md` — canonical build spec (ships to fellowship)
- `ARGOS_IMPROVEMENTS.md` — 50+ concrete advancements roadmap
- `ARGOS_PRO_UPGRADE_PLAN.md` — priority upgrade plan
- `ARGOS_SURGICAL_UPGRADES.md` — the surgical prescription derived from
  the 44-date ADBL run (see §11)
- `ARGOS_FREE_LLM_PROVIDERS.md` — the free-tier provider matrix
- `PROJECT_COMPARISON.md` — Argos vs. peer systems
- `FELLOWSHIP_MANIFEST.md` — INCLUDE/EXCLUDE for the fork
- `README.md` — private + fellowship variants

## 11. Repo/infra work

- Second remote `fellowship-fork` added; `fellowship` branch created by
  stripping EXCLUDE paths per the manifest (commit `77ebdef`).
- Fellowship-facing README rewritten and pushed to
  `fellowship-fork/main`.
- `REPO_TOPOLOGY.md` (companion doc) records the two-repo mechanism.

---

## What the 44-date ADBL live-fire run told us

We ran the full pipeline over 44 trading days on ADBL. The four
findings drive the current priority list:

1. **86% HOLD rate** — 38/44 dates. The machine is too paralyzed to be
   a trading system. Root cause: model-bias asymmetry between Bull
   and Bear (see #2).
2. **Bear was more confident than Bull on 43/44 dates** — Bear avg
   0.61 vs Bull avg 0.29, while realized returns for the same dates
   averaged +0.03% (mildly bullish reality). The gap is coming from
   the OpenRouter Nemotron model's baked-in bearish prior, not from
   evidence. Fix in flight: same-model debate + ensemble across two
   providers + blind judge.
3. **Kronos direction is ~50% accurate; magnitude is noise** (predicted
   −12.9%, realized −0.9%). Calibrated direction score is now what the
   technicals agent sees — raw % is journaled but not passed to the LLM.
4. **Sizing was uncorrelated with signal quality** — the weakest-signal
   date got the biggest bet. Kelly-fractional sizing wired through the
   Portfolio Manager fixes this.

Every fix above has a corresponding module already in the tree; the
next standup should be able to report *deltas from these fixes*, not
new plumbing.

---

## Immediate next steps (post-standup)

1. Same-model Bull/Bear debate + provider ensembling — code exists,
   needs the Portfolio Manager to consume ensembled confidence.
2. Cherry-pick everything INCLUDE-classified onto `fellowship` and
   push a fresh fork snapshot.
3. Decide on stub-vs-interface strategy for the fellowship fork so
   `pytest` runs green there (currently the graph imports EXCLUDE
   paths).
4. Re-run the 44-date ADBL slice with (1) and (3) landed and report
   HOLD-rate + directional-call-rate deltas.
