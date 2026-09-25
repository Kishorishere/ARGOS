# Argos — A Multi-Agent Trading Research Desk

> **Fuse-AI Fellowship showcase branch.** This branch contains the AI/agentic layer of the Argos project: the LangGraph orchestration, the specialist agent nodes, the bull/bear debate loop, the risk-manager veto, the portfolio-manager synthesis, and the prompt library. The data-plumbing, indicator engine, backtest simulator, and reporting layers live on the private branch and are not needed to read the architecture. This branch is a **code showcase, not a runnable end-to-end system** — imports into `argos.data.*`, `argos.indicators.*`, and similar excluded modules will be dangling. See `FELLOWSHIP_MANIFEST.md` for what is and isn't in scope.

Argos is a seven-agent research desk that debates a single trade idea per ticker, per day. Six of the seven agents are large language models performing different flavors of reasoning — fundamentals, sentiment, technicals, bullish advocacy, bearish advocacy, risk oversight, and final portfolio synthesis. A seventh gate short-circuits to HOLD when the Risk Manager vetoes.

## Architecture

```
                   ┌─────────────────┐
                   │  Ticker + date  │
                   └────────┬────────┘
                            │
      ┌─────────────┬───────┴────────┬─────────────┐
      ▼             ▼                ▼             ▼
┌───────────┐ ┌─────────────┐ ┌───────────┐ ┌───────────┐
│Fundamentals│ │  Sentiment  │ │Technicals │ │ Forecast  │
│  Analyst  │ │   Analyst   │ │  Analyst  │ │  (Kronos) │
└─────┬─────┘ └──────┬──────┘ └─────┬─────┘ └─────┬─────┘
      │              │              │              │
      └──────────────┼──────────────┼──────────────┘
                     ▼              ▼
                ┌────────────────────────┐
                │  Bull  vs  Bear debate │  ← N rounds, per-side memory
                └────────────┬───────────┘
                             ▼
                       ┌───────────┐
                       │   Judge   │  ← scores rebuttal quality, not conclusions
                       └─────┬─────┘
                             ▼
                     ┌───────────────┐
                     │ Risk Manager  │  ← veto authority → HOLD
                     └───────┬───────┘
                             ▼
                  ┌────────────────────┐
                  │ Portfolio Manager  │  ← final synthesis → BUY / SELL / HOLD
                  └──────────┬─────────┘
                             ▼
                       FinalDecision
```

## What's in this branch

| Directory | Purpose |
|---|---|
| `argos/graph.py` | LangGraph wiring — nodes, edges, parallel branches, conditional routes |
| `argos/model_router.py` | Free-tier LLM routing (Gemini via two Cloud projects) with fallback |
| `argos/schemas.py` | Pydantic v2 typed contracts for every agent's I/O |
| `argos/state.py` | Shared graph state passed between nodes |
| `argos/journal.py` | Structured JSON journal of every reasoning step for post-hoc debug |
| `argos/nodes/` | One file per specialist agent + the debate/judge/risk/portfolio orchestrators |
| `argos/ensemble/` | Aggregation over multiple LLM samples per decision |
| `argos/learn/` | Online signal ledger — feedback loop from realized outcomes |
| `argos/validation/` | Deflated Sharpe, purged k-fold — statistical honesty layer |
| `prompts/` | Persona prompts for each agent (bull/bear advocates, analysts, managers) |
| `tests/` | Unit tests for nodes, graph, debate loop, journal |
| `ARGOS_BUILD_SPEC.md` | The original architecture brief |
| `FELLOWSHIP_MANIFEST.md` | Scope definition for this branch |

## Hard constraints (design)

- **Free-tier APIs only.** Two Google Cloud projects, each with an independent Gemini free-tier quota; the model router load-balances across them.
- **Solo maintainer, ~3 hrs/day.** Favor simple, debuggable code over cleverness.
- **Scope fixed.** 1–3 tickers, one walk-forward backtest window. No multi-strategy, no portfolio-of-many, no live execution.
- **Pydantic-typed outputs everywhere.** No regex on free-text LLM responses.
- **LangGraph only.** No CrewAI, no AutoGen — one orchestration framework.

## Why the debate loop

A single LLM asked "should I buy?" is not a research desk; it's a coin flip with prose. The value comes from forcing the model into structured disagreement: Bull and Bear each get the same evidence pack, argue in rounds with per-side memory, and a Judge scores the *rebuttal quality* rather than the conclusions themselves. Only after that does the Risk Manager get to veto, and only after that does the Portfolio Manager synthesize. This is the same structure as the TradingAgents paper (Xiao et al., 2024) but re-implemented from scratch in LangGraph rather than their custom orchestrator.

## Reading the code

Start here in order:
1. `ARGOS_BUILD_SPEC.md` — the design brief.
2. `argos/schemas.py` — the vocabulary. Every node's I/O is here.
3. `argos/graph.py` — how the nodes connect.
4. `argos/nodes/debate.py` and `argos/nodes/judge.py` — the two-sided debate loop.
5. `prompts/bull_advocate.md` + `prompts/bear_advocate.md` — the actual reasoning prompts.
6. `argos/nodes/risk_manager.py` and `argos/nodes/portfolio_manager.py` — final synthesis.

## Not in this branch (private)

- NEPSE market data adapters (`argos/data/`) — OHLCV, floorsheet, sectors, Nepali sentiment, macro, corporate actions
- Technical indicator engine (`argos/indicators/`) — TA, patterns, Hurst, multi-timeframe
- Portfolio sizing, risk (GARCH, drawdown, correlation, vol target), sector screener, reports (`argos/{portfolio,risk,screener,reports}`)
- Fundamentals scoring and store (`argos/fundamentals/`)
- Backtest simulator (`argos/simulate.py`) and runner (`run_backtest.py`)
- Data ingest scripts and dashboards (`scripts/`)
- Research paper, literature review, proposal deck, Kronos Colab notebooks
- Internal roadmap docs

These are excluded because the fellowship is evaluating the agentic reasoning layer, not the NEPSE-specific plumbing.
