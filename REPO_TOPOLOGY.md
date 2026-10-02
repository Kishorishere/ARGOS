# Argos — Repo Topology & Sync Mechanism

Argos lives in **two GitHub repos** that share a single local working tree and
are wired to the same `.git` directory via two remotes. This is deliberate: the
project is simultaneously a personal research/IP effort and a Fuse-AI
fellowship submission, and the two audiences see different subsets of it.

---

## The two repos

| Remote name       | URL                                                                                | Purpose                                                                 | Contains                                                                                     |
|-------------------|------------------------------------------------------------------------------------|-------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| `origin`          | `github.com/Kishorishere/ARGOS.git`                                                | **Private / IP repo** — the real project, full scope                    | Everything: agents, prompts, NEPSE data pipeline, indicators, risk stack, backtester, reports, research paper, decks, planning docs, cached data |
| `fellowship-fork` | `github.com/fuseai-fellowship/A-Multi-Agent-Trading-Research-Desk-.git`            | **Fellowship submission** — public-facing, agent layer only             | Agent nodes, prompts, LangGraph orchestration, tests for agents, fellowship-facing README    |

Both remotes are configured on the same clone. `git remote -v` returns:

```
fellowship-fork    https://github.com/fuseai-fellowship/A-Multi-Agent-Trading-Research-Desk-.git
origin             https://github.com/Kishorishere/ARGOS.git
```

---

## Why split it this way

- **The private repo is the workspace.** All active development happens here.
  Data pipeline code, cached NEPSE floorsheet, corporate-action tables,
  broker-reputation heuristics, fundamentals scores, backtest engine,
  research paper drafts — none of it belongs on the fellowship fork.
- **The fellowship fork is a demonstrator.** It shows the reasoning
  architecture — how seven agents debate a trade — without shipping the
  data plumbing or the roadmap docs that would let someone else replicate
  the IP.
- **Two audiences, two threat models.** The fellowship fork is public inside
  the Fuse-AI org; the private repo is not. Anything sensitive (API keys,
  proprietary data, roadmap, upgrade plans, deck source, research draft)
  stays out of the fork by construction.

---

## Branch layout

Local branches:

```
* main           → tracks origin/main         (private, full project)
  fellowship     → tracks fellowship-fork/main (public, agent slice)
```

- `main` on `origin` is the source of truth.
- `fellowship` is a **filtered branch of `main`** — same history root, but
  with the EXCLUDE paths stripped. It is pushed to
  `fellowship-fork/main` so the fork looks like a normal repo to the
  Fuse-AI org.
- `origin/fellowship` also exists as a backup mirror of the filtered
  branch inside the private repo.

---

## What goes where

Governed by `FELLOWSHIP_MANIFEST.md`. Short version:

### Ships to fellowship fork (INCLUDE)

- `argos/graph.py`, `argos/model_router.py`, `argos/schemas.py`,
  `argos/state.py`, `argos/journal.py`
- `argos/nodes/` — all specialist and debate agents
- `argos/ensemble/`, `argos/learn/signal_ledger.py`, `argos/validation/`
- `prompts/` — every persona
- `tests/test_debate_loop.py`, `test_graph.py`, `test_journal.py`,
  `test_nodes.py` (agent-only tests)
- `pyproject.toml`, `requirements.txt`, `.gitignore`, `LICENSE`
- `ARGOS_BUILD_SPEC.md`, `.env.example`
- A **rewritten** fellowship-facing `README.md` — not the private one

### Stays private only (EXCLUDE)

- `argos/data/` — all NEPSE adapters (OHLCV, sectors, floorsheet, Kronos
  client, sentiment, broker patterns, corporate actions, macro, flow)
- `argos/indicators/`, `argos/fundamentals/`, `argos/screener/`,
  `argos/portfolio/`, `argos/risk/`, `argos/reports/`, `argos/simulate.py`
- `scripts/`, `run_backtest.py`, all Kronos Colab notebooks
- `main.tex`, `literature_review.tex`, `references.bib`,
  `Argos_Proposal_Deck.pptx`, `argos_deck_gen.py`, `plan.html`
- `ARGOS_PRO_UPGRADE_PLAN.md`, `ARGOS_SURGICAL_UPGRADES.md`,
  `ARGOS_IMPROVEMENTS.md`, `ARGOS_FREE_LLM_PROVIDERS.md`,
  `PROJECT_COMPARISON.md`, `PROGRESS_STANDUP.md`, this file
- `data/` — cached market data, floorsheet, calendars, signal ledger
- `logs/`, `.env`
- `tests/test_e2e.py`, `tests/test_flow_and_broker_signals.py`,
  `tests/test_indicators.py` (these exercise excluded modules)

---

## Sync workflow

1. **Work on `main` in the private repo.** Every commit lands here first.
2. **Classify each commit** by which paths it touches:
   - Purely INCLUDE paths → cherry-pick straight onto `fellowship`.
   - Purely EXCLUDE paths → do nothing on `fellowship`.
   - Mixed → either split the commit before pushing, or
     `git cherry-pick -n <sha>` onto `fellowship`, `git reset` the
     EXCLUDE hunks, then commit the INCLUDE remainder.
3. **Push private:** `git push origin main`.
4. **Push fellowship:** `git push fellowship-fork fellowship:main`.
5. **Pre-commit guard on `fellowship`:** a hook refuses any commit that
   reintroduces an EXCLUDE path. This is the last line of defense — the
   manifest is the first.

### Concrete example (already done once)

The initial fellowship push (`77ebdef` on the `fellowship` branch) was
produced by starting from `main`, `git rm`-ing every EXCLUDE path from
the manifest, rewriting the README for the fellowship audience, and
committing that as a single `chore(fellowship): strip …` commit. Future
syncs will be cherry-picks on top of that base rather than full re-strips.

---

## Handling code that imports across the boundary

`argos/graph.py` and the agent nodes import from `argos.data.*` and
`argos.indicators.*`, which are EXCLUDE. Two options exist (per manifest):

- **Option 1 — Fake stubs (fastest):** ship tiny `argos/data/`,
  `argos/indicators/` stubs on the fork that return typed dummy
  snapshots, so the graph is importable and unit-tests pass on the fork.
- **Option 2 — Interface refactor (cleaner):** hoist the data/indicator
  surfaces behind protocols; ship the interface + a fake impl on the fork,
  keep the real impl private.

Not yet chosen. Currently the fellowship fork carries the graph and nodes
without runnable data — a `pip install -e .` + `pytest` on the fork will
need stubs before the first re-sync.

---

## Guardrails (non-negotiable)

- **Never `git push` `main` to `fellowship-fork`.** The fork gets `fellowship`
  branch only, and only as `fellowship:main`.
- **Never commit `.env`, `data/`, or API keys.** Both remotes are covered
  by `.gitignore` but this is still a hand-audit before every push.
- **Never rewrite history on the fellowship fork after the reviewers see
  it.** Filtering happens *before* the first push, not after.
- **Commit hygiene stays the same on both.** No AI-attribution trailers
  on any commit or PR — the project is a fellowship submission and
  attribution to a code-assist model would misrepresent authorship.
