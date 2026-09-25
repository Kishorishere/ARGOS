# Argos — Multi-Agent Trading Research System
## Build Specification for Coding Agent

Paste this whole document to your coding agent (Claude Code / OpenCode) as the system-level brief. It's scoped as a sequence of phases — build and verify one phase before moving to the next. Do not skip ahead or add functionality not listed here.

---

## 1. Project Summary

Argos is a solo, free-tier, research-grade multi-agent system that produces a trading decision (BUY/SELL/HOLD + confidence + rationale) for 1–3 fixed tickers, evaluated over a single walk-forward backtest window. It is a **research and backtesting tool**, not a live trading system. No real orders are ever placed.

The architecture: five specialist agents run in parallel, feed a two-sided Bull/Bear debate, which feeds a Risk Manager (veto authority), which feeds a Portfolio Manager (final synthesis).

**Hard constraints — do not violate these:**
- Free-tier APIs only (two separate Google Cloud projects, each with its own Gemini API key)
- Solo maintainer, ~3 hrs/day of active dev time — favor simple, debuggable code over cleverness
- Scope is fixed: 1–3 tickers, one walk-forward backtest window. Do not build multi-strategy, multi-timeframe, or portfolio-of-many-tickers support.
- No live/paper trading execution layer in this phase. Output is a decision + rationale, logged — nothing is executed against a broker.

---

## 2. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | **LangGraph** (Python) | Native support for stateful graphs, parallel branches, conditional edges, and per-node model swapping — matches the TradingAgents-style architecture directly |
| LLM provider | **Google Gemini** via `langchain-google-genai` | Two Cloud projects = two independent free-tier quota pools (see §4) |
| Structured output | **Pydantic v2** models bound to each LLM call | Every agent must return a typed, validated object — no free-text parsing with regex |
| Config / secrets | `.env` file + `pydantic-settings` | Never hardcode API keys in source |
| Data | `pandas` + your existing OHLCV source | Reuse whatever loader you already have; don't build a new one |
| Testing | `pytest` | Unit tests per agent node using fixture data, not live API calls |
| Logging | Python `logging` module, structured JSON logs per run | You need to debug agent reasoning after the fact — log full state transitions |
| Environment | Python 3.11+, `venv` or `poetry` | Match whatever you're already using in Phase 2 of your attendance-evaluator work if you have a preference |

Do not introduce additional frameworks (no CrewAI, no AutoGen, no Haystack) — LangGraph alone covers this scope and mixing orchestration frameworks adds debugging surface you don't need.

---

## 3. Agent Roster & Responsibilities

| # | Agent | Role | Output type |
|---|---|---|---|
| 1 | Fundamentals Analyst | Reads fundamentals data (earnings, ratios, guidance) for the ticker | Structured signal: bullish/bearish/neutral + key factors |
| 2 | Sentiment Analyst | Reads news/sentiment data | Structured signal: sentiment score + key drivers |
| 3 | Technicals Analyst | Reads price/volume history (+ later, Kronos forecast) | Structured signal: technical read + key indicators |
| 4 | Bull Advocate | Argues the bull case using outputs of 1–3 | Structured argument: thesis + strongest 3 points |
| 5 | Bear Advocate | Argues the bear case using outputs of 1–3 | Structured argument: thesis + strongest 3 points |
| 6 | Risk Manager | Reviews the debate, has **veto authority** | Structured verdict: approve/veto/reduce-size + reasoning |
| 7 | Portfolio Manager | Final synthesis across everything above | Final decision: BUY/SELL/HOLD + confidence (0–1) + full rationale |

Each agent gets its **own** system prompt file (see §7) — do not share one mega-prompt across agents with role-switching. Separate prompts are easier to iterate on and keep each agent's context window smaller.

---

## 4. Model Routing (Two Gemini Projects)

You have two Google Cloud projects, each with an independent free-tier quota. Route by *traffic volume*, not by "importance," so your scarce Pro quota isn't consumed by high-frequency calls.

```
GEMINI_PROJECT_A_API_KEY   → Fundamentals, Sentiment, Technicals, Bull, Bear
                              (all on gemini-2.5-flash)

GEMINI_PROJECT_B_API_KEY   → Risk Manager (gemini-2.5-flash or flash-lite)
                              Portfolio Manager (gemini-2.5-pro)
```

Rationale: Project A absorbs the 5 high-volume analyst/debate calls per cycle. Project B is isolated so a rate-limit spike on Project A never blocks your Risk Manager veto or final synthesis. Pro is reserved for the single highest-leverage call (Portfolio Manager) since Pro's free-tier daily request cap is much lower than Flash's.

Implement this as a `model_router.py` module that maps `agent_name → (api_key_env_var, model_name)`. Do not hardcode model choice inside each agent's node function — keep it centrally configurable so you can rebalance later without touching agent logic.

I'll paste both `GEMINI_PROJECT_A_API_KEY` and `GEMINI_PROJECT_B_API_KEY` values when the coding agent's `.env` setup step asks for them — leave those as placeholders in `.env.example`, never commit real keys.

---

## 5. LangGraph State Schema

Define one `TypedDict` (or Pydantic model, if the agent prefers stronger validation) representing the full graph state. Every node reads from and writes to this state — don't pass ad hoc arguments between nodes.

```python
class FuseState(TypedDict):
    ticker: str
    as_of_date: str
    ohlcv_window: dict          # raw price/volume data for this cycle

    fundamentals_signal: dict | None
    sentiment_signal: dict | None
    technicals_signal: dict | None

    kronos_forecast: dict | None    # stub for now — see §8

    bull_argument: dict | None
    bear_argument: dict | None
    debate_round: int               # for multi-round debate, see below

    risk_verdict: dict | None
    final_decision: dict | None

    run_id: str
    errors: list[str]               # collect non-fatal node errors here, don't crash the graph
```

---

## 6. Graph Topology

```
                 ┌─────────────────┐
                 │   START (state)  │
                 └────────┬─────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
 Fundamentals        Sentiment          Technicals
   Analyst             Analyst           Analyst
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                  ┌───────────────┐
                  │  Debate Loop   │  (Bull ↔ Bear, N rounds,
                  │ Bull  ⇄  Bear  │   configurable, default 1–2)
                  └───────┬────────┘
                          ▼
                   Risk Manager
                  (approve / veto / resize)
                          │
              ┌───────────┴───────────┐
              │ veto?                 │ approved/resized
              ▼                       ▼
       final_decision =        Portfolio Manager
       HOLD (vetoed)            (final synthesis)
              │                       │
              └───────────┬───────────┘
                          ▼
                        END
```

Implementation notes:
- The three analyst nodes run **in parallel** (LangGraph supports this via multiple edges from one node into concurrent branches, joined before the debate node). Don't serialize them — they're independent.
- The debate loop should support a configurable number of rounds (start with 1 round: Bull argues, Bear rebuts, done). Keep it simple initially — you can extend to multi-round adversarial refinement later.
- Risk Manager veto short-circuits the graph — if vetoed, skip Portfolio Manager and set `final_decision` directly to HOLD with the veto reasoning attached. This saves you a Pro-tier call whenever the trade is already rejected.

---

## 7. Prompt & Output Contract Standards

For every agent:
1. **System prompt lives in its own file** under `prompts/{agent_name}.md` — not inline in Python strings. Makes iteration easier and keeps diffs clean.
2. **Every LLM call uses structured output** — bind a Pydantic schema via LangChain's `.with_structured_output()` (Gemini supports this). Never parse free-text JSON with `json.loads()` on unstructured completions.
3. **Every schema includes a `reasoning` or `key_factors` field** — you want auditable rationale, not just a label, both for debugging and because it's genuinely useful for a research system.
4. **Confidence scores are floats 0–1**, not vague labels like "high/medium/low" — you'll want numeric values for later backtest analysis.

Example schema shape (adapt per agent):
```python
class AnalystSignal(BaseModel):
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0, le=1)
    key_factors: list[str] = Field(max_length=5)
    reasoning: str
```

---

## 8. Kronos Integration Point (Stub Only — Do Not Build Yet)

Leave a clearly marked stub in the Technicals Analyst node and the Risk Manager node:

```python
# TODO(kronos): replace with real forecast once Kronos batch/live pipeline is ready.
# Expected shape: {"mean_forecast": [...], "uncertainty": [...], "uncertainty_normalized": [...]}
kronos_forecast = None
```

Do not wire up the actual Kronos notebook/API in this phase — that's deliberately deferred. Just make sure the state schema and node signatures already have a slot for it so integration later is a drop-in, not a refactor.

---

## 9. Backtesting Harness (Minimal, for This Phase)

- A simple runner script (`run_backtest.py`) that iterates over your fixed date range for each ticker, invokes the graph once per date, and logs the `final_decision` + full state to a JSON-lines file (one line per run).
- No portfolio simulation, P&L tracking, or position sizing math yet — that's a later phase. This phase is about getting correct, well-reasoned agent outputs per date, not computing returns.
- Store outputs somewhere queryable (JSONL is fine at this scale — don't set up a database for 1–3 tickers over one backtest window).

---

## 10. Testing Standards

- Unit test each agent node in isolation using **fixture** input data (a saved sample state), not live Gemini calls — keep tests fast and free.
- One integration test that runs the full graph on a single fixture date end-to-end, using a mocked/stubbed LLM (or, sparingly, one real call) to confirm the graph wires together correctly and the veto short-circuit path works.
- Test the veto path explicitly — construct a fixture where Risk Manager vetoes, confirm Portfolio Manager is skipped and `final_decision` is set correctly.

---

## 11. Project Structure

```
argos/
├── .env.example
├── pyproject.toml (or requirements.txt)
├── prompts/
│   ├── fundamentals_analyst.md
│   ├── sentiment_analyst.md
│   ├── technicals_analyst.md
│   ├── bull_advocate.md
│   ├── bear_advocate.md
│   ├── risk_manager.md
│   └── portfolio_manager.md
├── argos/
│   ├── state.py            # FuseState schema
│   ├── model_router.py     # agent_name -> (api_key, model) mapping
│   ├── schemas.py           # Pydantic output models per agent
│   ├── nodes/
│   │   ├── fundamentals.py
│   │   ├── sentiment.py
│   │   ├── technicals.py
│   │   ├── debate.py         # bull + bear
│   │   ├── risk_manager.py
│   │   └── portfolio_manager.py
│   ├── graph.py              # LangGraph graph definition
│   └── data/
│       └── ohlcv_loader.py   # wraps your existing data source
├── run_backtest.py
└── tests/
    ├── fixtures/
    ├── test_nodes.py
    └── test_graph.py
```

---

## 12. Build Order (Do Phases Sequentially)

1. **Scaffold** — project structure, `.env.example`, `model_router.py`, `state.py` (empty/stub nodes that just pass state through)
2. **One analyst end-to-end** — build Fundamentals Analyst fully (prompt, schema, node, unit test) — get one agent working correctly before replicating the pattern
3. **Remaining analysts** — Sentiment, Technicals (with Kronos stub), following the same pattern
4. **Debate loop** — Bull + Bear, single round first
5. **Risk Manager** — including the veto short-circuit logic in the graph
6. **Portfolio Manager** — final synthesis node
7. **Wire the full graph** — parallel analysts → debate → risk → portfolio, with conditional veto edge
8. **Backtest runner** — iterate over fixed dates, log JSONL output
9. **Review a handful of real outputs manually** — read the `reasoning` fields, sanity-check the debate is actually adversarial and not just two agents agreeing

Stop after step 9 and report back before touching Kronos live integration or any portfolio-level P&L math — those are separate follow-on phases.
