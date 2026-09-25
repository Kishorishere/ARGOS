# Fellowship Manifest

Defines the exact subset of this repo that ships to the Fuse-AI fellowship fork.
Rule of thumb: **agents + prompts + orchestration graph only**. No data plumbing,
no charts, no backtest scripts, no research writeups.

The `fellowship` branch is produced by starting from `main` and `git rm`-ing
everything not in the **Include** list. A pre-commit hook (see step 7 of the
plan) fails any push to `fellowship` that reintroduces an excluded path.

---

## INCLUDE (ships to fellowship fork)

### Core agent + orchestration code
- `argos/__init__.py`
- `argos/graph.py`               — LangGraph wiring of the agent team
- `argos/model_router.py`        — LLM provider routing / fallback
- `argos/schemas.py`             — pydantic schemas for agent I/O
- `argos/state.py`               — shared graph state
- `argos/journal.py`             — agent decision journaling
- `argos/nodes/`                 — all agent nodes
  - `debate.py`, `forecast.py`, `fundamentals.py`, `judge.py`,
    `portfolio_manager.py`, `risk_manager.py`, `sentiment.py`,
    `technicals.py`, `exit_manager.py`
- `argos/ensemble/`              — ensemble/source aggregation for LLM outputs
- `argos/learn/signal_ledger.py` — feedback loop for agent signals
- `argos/validation/`            — statistical validation of agent outputs
  (`deflated_sharpe.py`, `purged_kfold.py`)

### Prompts
- `prompts/` — every persona file
  (`bear_advocate.md`, `bull_advocate.md`, `exit_manager.md`,
   `fundamentals_analyst.md`, `portfolio_manager.md`, `risk_manager.md`,
   `sentiment_analyst.md`, `technicals_analyst.md`)

### Tests (agent-focused only)
- `tests/__init__.py`
- `tests/test_debate_loop.py`
- `tests/test_graph.py`
- `tests/test_journal.py`
- `tests/test_nodes.py`
- `tests/fixtures/`  (only the fixtures the above tests need — audit before include)

### Project config
- `pyproject.toml`
- `requirements.txt`  (may need pruning — drop deps only used by excluded modules)
- `.gitignore`
- `LICENSE`

### Docs (fellowship-facing rewrites)
- `README.md`               — **REWRITE** for fellowship audience (agent architecture, how to run the graph, how to swap LLM providers). Do not ship the current one.
- `ARGOS_BUILD_SPEC.md`     — architecture spec, keep as-is if it doesn't leak private roadmap
- `.env.example`            — audit and trim to only the LLM provider keys the fellowship code actually reads

---

## EXCLUDE (private-only, never pushed to fellowship fork)

### Data pipeline (chart/data-heavy)
- `argos/data/`                  — entire directory (NEPSE API, floorsheet, sectors, OHLCV, Kronos client, sentiment feeds, broker patterns, corporate actions, macro)
- `argos/indicators/`            — TA engine, patterns, Hurst, MTF, weights
- `argos/fundamentals/`          — scoring + store
- `argos/screener/`              — ranking
- `argos/portfolio/`             — sizing + state
- `argos/risk/`                  — GARCH, drawdown, correlation, vol target
- `argos/reports/`               — daily/weekly/monthly/dashboard report generation
- `argos/simulate.py`            — backtest engine

### Scripts and notebooks
- `scripts/`                     — entire directory (daily ingest, dashboard, screener backtest, ledger update, gemini fundamentals fetch)
- `run_backtest.py`
- `Argos_Kronos_Host.ipynb`
- `Kronos_Colab_Server.ipynb`
- `Kronos_Colab_Server (1).ipynb`

### Research / decks / paper
- `main.tex`
- `literature_review.tex`
- `references.bib`
- `Argos_Proposal_Deck.pptx`
- `argos_deck_gen.py`
- `plan.html`

### Internal planning docs (roadmap leaks)
- `ARGOS_PRO_UPGRADE_PLAN.md`
- `ARGOS_SURGICAL_UPGRADES.md`
- `ARGOS_IMPROVEMENTS.md`
- `ARGOS_FREE_LLM_PROVIDERS.md`
- `PROJECT_COMPARISON.md`

### Data + junk
- `data/`                        — all cached market data, floorsheet, calendars, announcements, sentiment corpora, signal ledgers
- `1xbet.apk`                    — unrelated binary, should be gitignored anyway
- `logs/`
- `.env`

### Excluded tests (they exercise excluded modules)
- `tests/test_e2e.py`                     — full pipeline; will fail without data/
- `tests/test_flow_and_broker_signals.py` — needs argos/data/
- `tests/test_indicators.py`              — needs argos/indicators/

---

## Ambiguous items resolved

Per your call: all "ambiguous" modules (backtest, indicators, screener,
portfolio, risk, reports, simulate, fundamentals) are **EXCLUDED**. The
fellowship version demonstrates the agentic reasoning layer only; it is
not a runnable end-to-end trading system.

**Consequence:** `argos/graph.py` and the agent nodes may import from
excluded modules (e.g. `argos.data.*`, `argos.indicators.*`). Before the
first fellowship push we will either:
1. Add lightweight stub modules under `argos/data/`, `argos/indicators/`,
   etc. that return typed fake data — so the graph can be *imported and
   unit-tested* on the fork even though it can't run against real markets, or
2. Refactor node imports behind an interface layer so the fork ships the
   interface + a fake implementation, and the private repo has the real one.

Option 1 is faster; option 2 is cleaner. Decide before the first sync.

---

## Sync workflow (reminder)

1. Work happens on `main` in the **private** repo.
2. When a commit touches only INCLUDE paths → `git cherry-pick` onto
   `fellowship`.
3. When a commit spans INCLUDE and EXCLUDE → split it, or cherry-pick
   with `-n` and `git reset` the excluded hunks.
4. Push `fellowship` to the org fork: `git push fellowship-fork fellowship:main`.
5. Pre-commit hook on `fellowship` refuses any excluded path.

---

## Open questions for you

- **README rewrite:** happy for me to draft a fellowship-facing README, or do you want to write it yourself?
- **Stub strategy:** option 1 (fake data modules) or option 2 (interface refactor) for the excluded-import problem?
- **Org repo name:** what will the fellowship fork be called under the Fuse-AI org? (Needed for the `fellowship-fork` remote URL.)
- **`.env.example` audit:** it currently has 3.2 KB of keys — many are probably for excluded modules (Gemini fundamentals fetcher, NEPSE APIs). Should I trim it to only what the agent graph reads?
