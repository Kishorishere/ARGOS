# Argos vs NEPSE Quant Terminal — Comparison & Merger Blueprint

> **Last updated:** September 2026 (brain-sharpening pass)

---

## What Changed in the Sept 2026 Sharpening Pass

The Argos codebase went through a "brain sharpening" pass before any wiring into NEPSE Quant Terminal. Every item below is now live in `argos/` and covered by tests (`pytest -q` → 67 passing):

- **Real technical-indicator engine** at `argos/indicators/` — RSI, MACD, ADX, Aroon, PSAR, EMA/SMA, Stochastic, StochRSI, Williams %R, ROC, Bollinger, Keltner, ATR, realized volatility, close z-score, OBV (with 10-bar slope), VWAP, CMF, MFI, volume z-score, classic + Fibonacci pivots, 20/50/200-day swing highs/lows, nearest S/R in ATR units, break-of-structure, 14 candlestick reversals with a composite `bull_ringer_score` / `bear_ringer_score`, and regular + hidden divergences of price vs RSI/MACD/OBV. Every fired signal carries `{indicator, value, threshold, direction, weight, note}`.
- **Technicals node rewritten** to compute the full `IndicatorSnapshot` AND call Kronos with uncertainty bands (`fetch_forecast_with_uncertainty`, previously dead code). The LLM now synthesizes both streams and must cite indicator values by name; the raw snapshot is attached to the signal for the journal.
- **Bull/Bear advocates see the full snapshot** — no more vibes-based debate. Prompts rewritten to require indicator-by-name citation.
- **Multi-round debate loop** — Bull ↔ Bear cycles for `MAX_DEBATE_ROUNDS` (default 2). Round-2 Bull explicitly rebuts round-1 Bear.
- **Portfolio state is real** — JSON-file-backed paper portfolio at `argos/portfolio/state.py` with cash %, sector weights, gross exposure %, per-position unrealized P&L. Risk Manager finally sees a truthful portfolio context instead of `{}`.
- **Structured decision journal** at `argos/journal.py` — append-only (was silently overwriting), unique `attempt_id` per run, `argos_version` (git SHA), per-agent duration, and a flat `indicator_signals_fired` list with each signal's contribution to the composite bias score.
- **Backtest hardened** — `--force` to re-run, `--simulate` runs paper P&L via `argos/simulate.py` (bps slippage + commission, hit-rate, avg holding return), Gemini `max_retries`/timeout wired via env, holiday-aware trading date filter.
- **State reducer bug fixed** — parallel analysts had concurrent writes to `analyst_prompts` that would have crashed on the first live run; `analyst_prompts` now has a merge reducer.
- **Rename** — the whole codebase went from `FUSE-005` / `fuse005` to `Argos` / `argos`; the "six-agent" README claim is now correct at seven.

---

## TL;DR

| | **Argos** | **NEPSE Quant Terminal (NQT)** |
|---|---|---|
| **Market** | Global (US stocks via yfinance) — NEPSE wiring pending | Nepal (NEPSE exclusively) |
| **Purpose** | Research brain — produce a BUY/SELL/HOLD with a full indicator + debate audit trail | Full trading terminal — signals, paper trading, portfolio, TUI dashboard, MCP |
| **Agent System** | LangGraph + Gemini (7 agents; parallel analysts; N-round Bull/Bear debate; hard Risk veto) | Custom agent graph (evidence-gated research, debate, risk committee, portfolio) |
| **Technicals** | Hand-rolled indicator engine (RSI, MACD, ADX, BB, Keltner, ATR, OBV, VWAP, CMF, MFI, S/R, 14 candle patterns, divergences) + Kronos w/ uncertainty | Inline indicators in `simple_backtest.py` (RSI + realized vol); no MACD/BB/ATR/ADX yet |
| **Data** | yfinance OHLCV + Gemini web search grounding | SQLite DB (456K+ rows), NEPSE scrapers, floorsheet, earnings, sentiment, macro |
| **Execution** | None — journals decisions only | Paper trading engine with P&L, NAV, multi-account, stop losses |
| **Validation** | Indicator unit tests + look-ahead invariance + mocked E2E | Full suite: walk-forward, Monte Carlo, CSCV/PBO, deflated Sharpe, random baseline |
| **UI** | None (CLI backtest runner) | Textual TUI with 9 tabs, keyboard-driven |
| **Tech Stack** | LangGraph, LangChain, Gemini, Pydantic v2 | Textual, pandas, SQLite, nepse API, Ollama/Gemma/Claude |

---

## What Argos Has That NQT Doesn't

### 1. LLM-Powered Multi-Agent Reasoning (Gemini)

Argos's core strength is its **7-agent pipeline** powered by Google Gemini:

```
Fundamentals Analyst ──┐
Sentiment Analyst    ───┤──▶ Bull Advocate ──▶ Risk Manager ──▶ Portfolio Manager
Technicals Analyst   ──┘       ▲                                    (FINAL DECISION)
                           Bear Advocate
                           (rebuttal)
```

- **Web search grounding**: Fundamentals and Sentiment agents search Google in real time for earnings data, analyst ratings, news, and social media sentiment — no pre-built data pipeline needed.
- **Kronos integration**: Technicals agent narrates a time-series foundation model's price forecast with uncertainty bands.
- **Adversarial debate**: Bull and Bear advocates argue from analyst outputs, with Bear directly rebutting Bull's specific claims.
- **Risk Manager veto**: Has unconditional veto authority — can short-circuit the entire pipeline to HOLD.

**NQT has no LLM-powered analysis.** Its agent system (`nepse_agents/`) is rule-based with hardcoded thresholds (score > 0.72 → "Buy", score >= 0.60 → "Overweight"). No web search, no structured LLM output, no real-time reasoning.

### 2. Structured Output with Reasoning Trails

Every Argos agent returns a **Pydantic-validated structured output** with:
- Signal (bullish/bearish/neutral) or action (buy/sell/hold)
- Confidence score (0.0–1.0)
- Key factors list
- Full reasoning rationale

This means every decision is **auditable** — you can read *why* the system made a particular call.

### 3. Google Search Grounding (Real-Time Data)

The Fundamentals and Sentiment agents use Gemini's native `google_search` tool to:
- Search for real quarterly earnings, P/E ratios, revenue growth
- Find analyst upgrades/downgrades, price targets
- Pull social media sentiment (Reddit, X)
- Get recent news headlines

**This eliminates the need for separate data APIs** (SEC EDGAR, NewsAPI, Reddit API, etc.).

### 4. Model Router (Two-Project Quota Isolation)

Argos routes LLM calls across **two separate Google Cloud projects**:
- Project A: High-volume analyst/debate calls (gemini-2.5-flash)
- Project B: Risk Manager + Portfolio Manager (isolated quota)

This prevents rate-limit spikes on one project from blocking critical decisions.

### 5. Modular Prompt Architecture

Each agent has its own system prompt in `prompts/{agent_name}.md` — easy to iterate, diff, and A/B test without touching Python code.

---

## What NQT Has That Argos Doesn't

### 1. Complete NEPSE Data Infrastructure

NQT has a **full data pipeline** for the Nepal Stock Exchange:

| Data Source | Module | What It Provides |
|---|---|---|
| **OHLCV Prices** | `database.py`, `data_io.py` | 456K+ rows of historical price data for all NEPSE symbols |
| **Floorsheet Data** | `floorsheet_scraper.py`, `floorsheet_ingestion.py` | Broker-level trade flow (who bought, who sold, at what price) |
| **Quarterly Earnings** | `quarterly_reports.py`, `earnings_ingestion.py` | EPS, revenue from quarterly filings |
| **Financial Reports** | `financial_reports.py` | Balance sheet, P&L, cash flow data |
| **Corporate Actions** | `corporate_actions.py` | Dividends, bonuses, rights, splits |
| **Sentiment** | `sentiment_ingestion.py`, `nepali_sentiment.py` | Nepali-language news sentiment |
| **Macro Data** | `macro_signals.py`, `remittance_ingestion.py` | Remittance flows, macro indicators |
| **Gold/Silver** | `gold_silver_ingestion.py`, `gold_hedge.py` | Precious metals regime for capital deployment |
| **Rainfall/Hydropower** | `rainfall_ingestion.py`, `satellite_data.py` | Hydropower generation signals |
| **Indian Markets** | `indian_data_download.py` | Cross-border market correlation |
| **Real-time Quotes** | `realtime_market.py`, `vendor_api.py` | Live NEPSE prices via unofficial API |

**Argos has none of this.** It uses yfinance (which doesn't cover NEPSE) and Gemini web search (which may not have granular NEPSE data).

### 2. Paper Trading Engine (Full Execution)

NQT has a complete paper trading system:

- **Multi-account support** — each account has its own portfolio, trade log, NAV history
- **Auto-trading engine** — generates signals every 5 trading days, manages entries/exits
- **Stop losses** — hard stop, trailing stop, profit target
- **Holding periods** — configurable per strategy
- **Regime filter** — adjusts position counts based on bull/bear/neutral market
- **Order book** — buy/sell order management with fill tracking
- **MeroShare import** — seed portfolios from real broker holdings

**Argos has no execution layer.** It logs decisions but never tracks positions or P&L.

### 3. Statistical Validation Suite

NQT ships with a comprehensive validation framework:

| Test | What It Does |
|---|---|
| **Walk-Forward** | Slides train/test windows across 6+ years, stitches out-of-sample equity curves |
| **Monte Carlo** | Simulates thousands of random trade sequences to assess luck vs. skill |
| **CSCV/PBO** | Combinatorially Symmetric Cross-Validation for overfitting detection |
| **Deflated Sharpe** | Adjusts Sharpe ratio for multiple testing / strategy variants tried |
| **Random Baseline** | Compares strategy returns against random entry/exit |
| **Transaction Costs** | SEBON levy, broker commission, DP charges for NEPSE |
| **Slippage Model** | Estimates price impact on illiquid NEPSE names |
| **Regime Stress** | Tests strategy under different market regimes |
| **Sensitivity Analysis** | Parameter sensitivity to identify robust vs. fragile configs |

**Argos has no validation.** The backtest runner just logs decisions — no statistical significance testing.

### 4. TUI Dashboard (Full UI)

NQT runs entirely in the terminal with a **9-tab Textual dashboard**:

- Market Overview (quotes, 52-week, top movers, sector heatmap)
- Portfolio (positions, P&L, NAV history)
- Order Book (manual paper trades)
- Strategy Builder (create/backtest/assign strategies)
- Agent (AI analysis — Ollama/Gemma/Claude)
- Watchlist
- Trade History
- Signal Scanner
- Account Management

**Argos has no UI** — it's a CLI backtest runner.

### 5. Strategy Builder & Registry

NQT allows users to:
- Create custom strategies with configurable signal types
- Set holding days, stop losses, trailing stops, max positions
- Assign different strategies to different accounts
- Backtest any strategy against 6+ years of data

### 6. NEPSE-Specific Features

- **NEPSE Calendar** — trading days, public holidays, weekmask (Sun-Thu or Mon-Fri)
- **Circuit Breaker Logic** — ±10% price bands enforced in backtests
- **Sector Mapping** — Commercial Banks, Hydropower, Microfinance, etc.
- **Gold Hedge Overlay** — adjusts capital deployment based on gold/silver regime
- **Lot Size Handling** — NEPSE requires round lots of 10 kitta
- **Telegram Alerts** — real-time notifications via Telegram bot

### 7. MCP Server (AI Agent Integration)

NQT exposes an MCP (Model Context Protocol) server so AI agents (Claude, Codex, etc.) can query market data, portfolios, and signals programmatically.

---

## Feature Gap Matrix

| Feature | Argos | NQT | Merger Opportunity |
|---|---|---|---|
| **Multi-agent LLM reasoning** | ✅ Gemini + LangGraph | ❌ Rule-based only | Add Gemini agents to NQT |
| **Web search grounding** | ✅ Real-time Google Search | ❌ Static DB only | NQT agents search for live context |
| **Kronos time-series forecast** | ✅ (stub, ready to wire) | ❌ | Feed Kronos into NQT signal engine |
| **NEPSE OHLCV data** | ❌ yfinance (no NEPSE) | ✅ 456K+ rows, SQLite | Use NQT's DB as Argos's data source |
| **NEPSE fundamentals** | ❌ | ✅ Earnings, financials, corporate actions | Feed into Argos's fundamentals agent |
| **NEPSE sentiment** | ❌ | ✅ Nepali news, social media | Feed into Argos's sentiment agent |
| **Floorsheet / broker flow** | ❌ | ✅ | New evidence source for agents |
| **Paper trading engine** | ❌ | ✅ Full P&L, multi-account | Argos decisions → NQT execution |
| **Auto-trading engine** | ❌ | ✅ Signal-driven, regime-aware | Argos replaces signal engine |
| **Statistical validation** | ❌ | ✅ Walk-forward, Monte Carlo, CSCV | Validate Argos decisions |
| **TUI dashboard** | ❌ | ✅ 9-tab terminal UI | Display Argos agent outputs |
| **Strategy builder** | ❌ | ✅ JSON config, per-account | Argos as a strategy backend |
| **Gold hedge / macro** | ❌ | ✅ Regime detection, macro signals | New evidence sources for agents |
| **MCP server** | ❌ | ✅ | Expose Argos agents via MCP |
| **Telegram alerts** | ❌ | ✅ | Send Argos decisions to Telegram |
| **Model router (quota isolation)** | ✅ Two-project Gemini routing | ❌ | Reuse for NQT agents |

---

## Effort to Incorporate NEPSE Data into Argos

### Option A: Standalone (Just add NEPSE data to Argos)

**Effort: Medium (2–3 days)**

1. **Write a NEPSE OHLCV loader** — query NQT's SQLite DB instead of yfinance
   - Create `argos/data/nepse_loader.py` that reads from `nepse_market_data.db`
   - Map NEPSE symbol names to the correct table/column format
   - Enforce the same no-lookahead guard as the yfinance loader

2. **Wire NEPSE fundamentals into the fundamentals agent** — either:
   - **Option A1 (quick)**: Use Gemini web search to find NEPSE earnings data (already works, no code change needed — just update prompts)
   - **Option B (robust)**: Query NQT's SQLite for quarterly reports and feed into the prompt

3. **Wire NEPSE sentiment** — similar approach:
   - Gemini web search for NEPSE news (quick)
   - Or use NQT's `sentiment_ingestion.py` data (robust)

4. **Update prompts** — mention NEPSE-specific context (lot sizes, circuit breakers, sector names)

5. **Update backtest runner** — accept NEPSE symbols, use NEPSE calendar

**What you get:** Argos can produce BUY/SELL/HOLD decisions for NEPSE tickers, with Gemini-powered analysis. But no paper trading, no validation, no dashboard.

### Option B: Full Merger (Combine both projects)

**Effort: Large (1–2 weeks)**

This is the recommended path. Here's the architecture:

```
┌─────────────────────────────────────────────────────────────────┐
│                    UNIFIED TRADING PLATFORM                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────┐  ┌─────────────────────────────┐ │
│  │   NEPSE Quant Terminal   │  │     Argos Agent Engine   │ │
│  │                          │  │                             │ │
│  │  TUI Dashboard (9 tabs)  │  │  Fundamentals ─┐            │ │
│  │  Paper Trading Engine    │  │  Sentiment    ──┤            │ │
│  │  Auto-Trading Engine     │  │  Technicals   ──┤            │ │
│  │  Strategy Builder        │  │                 ▼            │ │
│  │  Validation Suite        │  │  Bull/Bear Debate            │ │
│  │  NEPSE Data Pipeline     │  │       │                     │ │
│  │  Gold Hedge Overlay      │  │       ▼                     │ │
│  │  Telegram Alerts         │  │  Risk Manager               │ │
│  │  MCP Server              │  │       │                     │ │
│  │                          │  │       ▼                     │ │
│  └──────────┬───────────────┘  │  Portfolio Manager          │ │
│             │                  │  (BUY/SELL/HOLD + rationale)│ │
│             │                  └──────────┬──────────────────┘ │
│             │                             │                     │
│             └──────────┬──────────────────┘                     │
│                        ▼                                        │
│             ┌─────────────────────┐                             │
│             │   Decision Router   │                             │
│             │                     │                             │
│             │  Argos says BUY? │──▶ Paper Trading Engine     │
│             │  Argos says SELL?│──▶ Exit Position            │
│             │  Argos says HOLD?│──▶ Do nothing               │
│             │  Argos vetoes?   │──▶ Skip (HOLD)             │
│             └─────────────────────┘                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Merger Implementation Plan

#### Phase 1: Data Bridge (Day 1–2)

| Task | Details | Effort |
|---|---|---|
| Create `nepse_loader.py` | Read OHLCV from NQT's SQLite DB, return dict format matching Argos's `load_ohlcv()` | 2 hours |
| Create `nepse_fundamentals_loader.py` | Query quarterly reports, financial data from NQT's DB | 3 hours |
| Create `nepse_sentiment_loader.py` | Query sentiment data from NQT's DB, plus Gemini web search for补充 | 2 hours |
| Create `nepse_macro_loader.py` | Query macro signals, gold hedge, remittance from NQT's DB | 2 hours |
| Update `run_backtest.py` | Add `--market nepse` flag, use NEPSE loaders and calendar | 2 hours |

#### Phase 2: Agent Enrichment (Day 3–5)

| Task | Details | Effort |
|---|---|---|
| Update fundamentals prompt | Add NEPSE-specific context (sector names, lot sizes, circuit breakers) | 1 hour |
| Update sentiment prompt | Add Nepali news sources, NEPSE-specific social media | 1 hour |
| Add floorsheet evidence | New input to agents — broker flow data (who's buying/selling) | 3 hours |
| Add macro evidence | Gold hedge regime, remittance flows, Indian market correlation | 2 hours |
| Wire Kronos for NEPSE | Load NEPSE OHLCV into Kronos forecast pipeline | 3 hours |
| Update Technicals analyst prompt | Include Kronos forecast + NEPSE-specific technical context | 1 hour |

#### Phase 3: Decision Execution (Day 6–8)

| Task | Details | Effort |
|---|---|---|
| Create decision executor | Map Argos decisions → NQT paper trading API | 3 hours |
| Add position tracking | Track current positions so SELL decisions close real paper positions | 4 hours |
| Add portfolio state to FuseState | Inject current portfolio state into the graph | 2 hours |
| Update Risk Manager | Factor in current position exposure, drawdown, sector concentration | 2 hours |

#### Phase 4: Validation & UI (Day 9–12)

| Task | Details | Effort |
|---|---|---|
| Run validation suite | Walk-forward, Monte Carlo on Argos decisions | 3 hours |
| Add Argos tab to TUI | New tab showing agent outputs, reasoning trails, debate logs | 4 hours |
| Add Telegram integration | Send Argos decisions to Telegram | 2 hours |
| Add MCP endpoint | Expose Argos agents via MCP for AI agent access | 2 hours |

---

## What the Combined System Would Look Like

### The Best of Both Worlds

**From Argos:**
- LLM-powered multi-agent reasoning with real-time web search
- Adversarial Bull/Bear debate
- Risk Manager veto authority
- Structured output with full reasoning trails
- Kronos time-series forecasting
- Two-project Gemini quota isolation

**From NQT:**
- Complete NEPSE data infrastructure (456K+ rows, live scraping)
- Paper trading engine with P&L tracking
- Auto-trading engine with regime-aware position management
- Statistical validation suite (walk-forward, Monte Carlo, CSCV)
- TUI dashboard with 9 tabs
- Strategy builder and registry
- Gold hedge overlay, macro signals, sector analysis
- Telegram alerts, MCP server

### Use Cases Unlocked

1. **"Should I buy NABIL today?"** → Argos agents search for NABIL's latest earnings, analyst ratings, Nepali news sentiment, broker flow data, Kronos price forecast → produce a BUY/SELL/HOLD with full rationale → auto-execute on paper trading engine

2. **"Backtest my strategy on NEPSE"** → Run Argos agents on 6 years of NEPSE data → validate with Monte Carlo, walk-forward, CSCV → see if LLM-powered decisions beat rule-based signals

3. **"What's the market sentiment right now?"** → Dashboard shows real-time agent outputs, debate logs, risk assessments for your watchlist

4. **"Alert me when the system says BUY"** → Telegram notification with full reasoning trail when Argos triggers a paper trade

---

## Recommendation

**Go with Option B (Full Merger).** Here's why:

1. **NQT already has everything Argos lacks** — data pipeline, execution, validation, UI
2. **Argos already has everything NQT lacks** — LLM reasoning, web search, structured debate
3. **They're complementary, not overlapping** — different tech stacks, different markets, different strengths
4. **The merger creates something neither has alone** — an LLM-powered, fully validated, paper-traded NEPSE trading platform

**Start with Phase 1 (Data Bridge) — it's the lowest effort with the highest immediate value.** Once Argos can read NEPSE data, you can run the full agent pipeline on Nepal stocks without any other changes.

---

## Files to Create for Phase 1

```
argos/data/
├── nepse_loader.py           # Read OHLCV from NQT's SQLite
├── nepse_fundamentals_loader.py  # Read earnings, financials
├── nepse_sentiment_loader.py     # Read sentiment data
└── nepse_macro_loader.py         # Read macro, gold, remittance
```

Plus updates to:
- `run_backtest.py` — add `--market nepse` flag
- `prompts/fundamentals_analyst.md` — NEPSE-specific context
- `prompts/sentiment_analyst.md` — Nepali news sources
- `.env.example` — add `NEPSE_DB_PATH` config

---

## Argos → NQT Integration Contract (target for the next phase)

Once the brain is judged sharp enough on NEPSE OHLCV, the handoff to NQT is a mechanical mapping: NQT's paper-agent graph already accepts a `SignalEnvelope` and returns a `PortfolioDecision`, so an Argos "adapter" just needs to project the journal record onto that schema.

| NQT `SignalEnvelope` field | Argos source (per journal record) |
|---|---|
| `symbol` | `ticker` |
| `as_of_date` | `date` |
| `sector` | `portfolio_state.positions[*].sector` if held, else looked up |
| `signal_id` | `attempt_id` (already a UUID) |
| `strategy_id` | Constant: `"argos-v1"` (bump when the brain changes) |
| `score` | `indicator_composite.net_bias_score` normalized to `[-1, 1]` |
| `strength` | `final_decision.confidence` × sign(action) |
| `confidence` | `final_decision.confidence` |
| `rank` | Assigned at portfolio level, not per-decision (leave `None`) |
| `regime` | `indicator_composite.trend_regime` (`uptrend`/`downtrend`/`chop`) |
| `suggested_quantity` | Derived from `final_decision.position_size` × NQT account equity |
| `suggested_limit_price` | `ohlcv_summary.last_close` (or Kronos p50 forecast) |
| `suggested_horizon_days` | `kronos_forecast.pred_len` if present, else `5` |
| `thesis` | `final_decision.rationale` |
| `catalysts` | `analyst_signals.fundamentals.key_factors` + `sentiment.key_factors` |
| `risks` | `risk_verdict.risk_factors` |
| `evidence_refs` | `[{"kind": "argos_journal", "path": "<journal path>", "attempt_id": <id>}]` |
| `metadata` | The full `indicator_composite` block + `debate.rounds_completed` |

### Integration surfaces (choose one when phase starts)
- **MCP client** — Argos calls `run_multi_agent_decision(candidate)` on NQT's FastMCP server (`apps/mcp/server.py`, default `http://127.0.0.1:8765/mcp`). NQT still owns execution + policy checks.
- **File drop** — Argos writes `SignalEnvelope` JSON into a folder NQT watches. Zero coupling.
- **In-process** — NQT imports Argos as a library, calls `graph.invoke(state)`, adapts the result. Tightest reuse of NQT's data adapters (Merolagani OHLCV, ShareSansar fundamentals).

Nothing below the "Sharpening Pass" section above is new — Argos's brain is now ready to be judged on real NEPSE data. The integration will consist of (a) swapping `argos/data/ohlcv_loader.py` for a NEPSE loader that hits NQT's SQLite (or the user's own NEPSE API), (b) adding NEPSE-flavored context to the fundamentals/sentiment prompts, and (c) writing the `journal_to_signal_envelope` adapter.
