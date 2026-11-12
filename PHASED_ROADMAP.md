# Phased Roadmap — Argos Research Desk

*Companion to `TRADER_PERSPECTIVE_ANALYSIS.md`. Written 2026-11-09. This is the actionable plan. No code has been changed yet; this file exists so we can agree on sequence before any edits start.*

---

## 0. Guiding principles (locked after the last message)

Three rules every phase respects. If a proposed change violates one, it doesn't ship.

1. **The agentic pipeline is not replaced; it is promoted.** The multi-agent graph in `argos/graph.py` stays exactly as it is. Every panel in the new UI is paired with the agent that already reasons over that panel's data. A Fundamentals panel shows P/E, EV/EBITDA, next earnings — *and under it* the Fundamentals analyst's structured verdict (signal, confidence, key_factors, reasoning). Same for Sentiment, Technicals, Risk. The agents become **the researchers for the trader**, which is the Fuse-fellowship thesis, finally made visible.
2. **Journals are the single source of truth.** Everything the UI shows — backtest tables, live panels, decision dots on the chart, calibrated confidence, cost meter — reads from the JSONL journals under `logs/decisions/`. No panel invents data; every panel is a view over the journal. This keeps the paper's reproducibility story intact.
3. **The paper and the trader are the same reader with two hats.** A phase that strengthens the trader's surface must also strengthen the paper's measurements. If it only helps one side, it waits. This is why Phase D (ablations) is not deferred to last — it rides inside each other phase's acceptance criteria.

---

## 1. The unified screen — what we are building toward

Before phasing, here is the target the phases are converging on. Once this exists, we stop.

### Per-ticker page (the heart of the product)

```
┌────────────────────────────────────────────────────────────────────────┐
│  NVDA · $942.14 (+2.1%)  ·  as of 2026-11-09 15:42 ET  ·  LIVE / PAPER │
├─ Band 1: CHART ────────────────────────────────────────────────────────┤
│                                                                        │
│   Candles + MA(20/50/200) + Bollinger(20,2)                            │
│   Past decision dots overlaid (green=BUY, red=SELL, grey=HOLD,         │
│       hollow=gate-skipped; opacity = confidence)                       │
│   Sub-pane: RSI(14) with 30/70 lines, MACD                             │
│   Click a dot → journal drawer slides in from the right                │
│                                                                        │
├─ Band 2: PANELS (4 columns) ───────────────────────────────────────────┤
│                                                                        │
│  FUNDAMENTALS       │ TECHNICALS       │ SENTIMENT        │ RISK       │
│  P/E 42.1           │ RSI(14) 68.3     │ News (last 10)   │ Position   │
│  Fwd P/E 31.7       │ Bollinger %B 0.9 │ Reddit tone +0.3 │ Corr SPY   │
│  EV/EBITDA 29.4     │ MACD +4.2        │ X cashtag vol    │ Drawdown   │
│  Next earnings 7d   │ 20D vol 24.1%    │ Analyst upgrades │ Sector exp │
│  ──────────────     │ ──────────────   │ ──────────────   │ ────────── │
│  AGENT VERDICT      │ AGENT VERDICT    │ AGENT VERDICT    │ AGENT      │
│  bullish, 0.72      │ bearish, 0.61    │ neutral, 0.55    │ APPROVED   │
│  "forward growth    │ "RSI overbought  │ "mixed — AI      │ "size 0.08 │
│   priced; hold"     │  into earnings"  │  tailwind vs..." │  of NAV"   │
│                                                                        │
├─ Band 3: COMMITTEE ────────────────────────────────────────────────────┤
│                                                                        │
│   Bull transcript  |  Bear transcript  |  Judge verdict                │
│   Portfolio Manager final decision: BUY, 0.68 conf, 0.6% of NAV        │
│   Calibrated to historical: 64% directional accuracy at h=5            │
│   [Rerun on current snapshot]  [I disagree — log override]             │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

### Dashboard page (watchlist grid)

One row per ticker. Sortable, filterable. Click a row → opens the per-ticker page above.

```
TICKER  SPARK   Δ      RSI14   IV-RK   GATE    LAST VERDICT   NEXT EVENT       🔔
NVDA    ╱╲╱    +2.1%   68.3    42      AWAKE   BUY 0.72       Earnings 7d      ●
TSLA    ╲╱╲    -1.4%   31.2    78      SILENT  HOLD           —                ·
ORCL    ╱╱╱    +0.6%   56.1    35      AWAKE   HOLD 0.51      Ex-div 3d        ·
KO      ────   +0.1%   49.8    22      SILENT  —              Dividend 12d     ·
```

That is the full surface. Everything below is how we get there without breaking the pipeline or the paper.

---

## 2. Phase 0 — plumbing (foundational, ~1 weekend)

**Goal.** Make the pipeline addressable the way the UI needs it: on demand, per ticker, with a response shape the panels can slot into. Nothing user-facing ships this phase; it just unblocks everything else.

**Why it's first.** Right now the pipeline writes journals as a side effect of a backtest loop. There is no single endpoint a UI can call that says "run the full committee on NVDA with live data and give me back one structured response." Phase 0 adds exactly that one thing.

**Scope.**

- **0.1** New service: `server/services/snapshot.py` — collects *raw data only* for a ticker (indicators from `argos/indicators/engine.py`, fundamentals from `argos/fundamentals/store.py`, portfolio context from `argos/portfolio/`, correlations from `argos/risk/correlation.py`). No LLM calls. Fast (<2s).
- **0.2** New route: `GET /api/snapshot/{ticker}?as_of=YYYY-MM-DD` → returns the snapshot above as JSON. Defaults to today when `as_of` omitted.
- **0.3** New route: `POST /api/analyze/{ticker}` — runs the full LangGraph pipeline with live data (`argos/data/ohlcv_loader.py` already handles both backtest and live dates), returns the final state + journal path. Streams via SSE identically to the existing `/api/runs/{id}/stream`.
- **0.4** New route: `GET /api/decisions/{ticker}?from=&to=` — walks the journals under `logs/decisions/**/{ticker}/` and returns a thin array of `{as_of, action, confidence, gate_state, journal_path}` rows. This is what the chart overlay consumes.
- **0.5** Extend `argos/journal.py` to also journal **live** runs under `logs/decisions/live/{ticker}/{ticker}_{date}.jsonl`. Same schema, new namespace. The backtest tables are untouched.

**Reuse map.** We write ~300 LOC of new code and touch nothing in `argos/graph.py`, `argos/nodes/*`, or any prompt file.

**Research angle preserved.** Phase 0 doesn't add or change any measurement. The paper's tables keep reading from `logs/decisions/nasdaq_focus/` exactly as before.

**Exit criteria.** `curl POST /api/analyze/NVDA` returns a committee verdict from today's data in <60s; `curl GET /api/snapshot/NVDA` returns a filled JSON <2s; the backtest harness still runs unchanged.

---

## 3. Phase A — the trader screen, v1 (~2 weekends)

**Goal.** Turn the per-ticker page sketch in Section 1 into real UI. This is the phase that transforms the project from "backtest report" to "research desk." Every subsequent phase builds on this frame.

**Scope.**

### A.1 — Chart band (highest-leverage single item in the whole roadmap)

- Install `lightweight-charts` in `frontend/package.json`.
- New component `frontend/src/components/TickerChart.tsx`. Props: `ticker`, `decisions[]`, `indicators{}`.
- Candle data: pull from the OHLCV loader via a new `GET /api/ohlcv/{ticker}?from=&to=&tf=` route. The loader already supports multiple timeframes.
- Overlays: MA(20), MA(50), MA(200), Bollinger(20, 2). Toggleable.
- Decision dots: one per journalled decision in the current chart window. Color = `action`, opacity = `confidence`, hollow = `gate_verdict.proceed == false`. Hover = tooltip with date/action/confidence. Click = open the journal drawer.
- Sub-panes under the main chart: RSI(14) with 30/70 reference lines; MACD histogram.

### A.2 — The four panels (Fundamentals, Technicals, Sentiment, Risk)

Each panel has the same **two-layer shape**, and this is the most important design point in the phase:

```
┌─ FUNDAMENTALS ────────────────────┐
│  RAW NUMBERS                      │
│    P/E (TTM)        42.1          │
│    P/E (Forward)    31.7          │
│    EV / EBITDA      29.4          │
│    P/B              52.8          │
│    FCF yield         1.8 %        │
│    Next earnings    2026-11-16    │
│    Dividend yield    0.03 %       │
│                                   │
│  ─────────────────────────────    │
│  AGENT VERDICT                    │
│    Signal:     bullish            │
│    Confidence: 0.72               │
│    Key factors:                   │
│      • forward P/E discount       │
│      • FCF acceleration           │
│      • guidance raised last Q     │
│    Reasoning:                     │
│    "Forward multiple compressed   │
│     while FCF growth is still     │
│     >35%, the premium is earned." │
│    [View full prompt + call] ↗    │
└───────────────────────────────────┘
```

Top half is the **data a trader expects to see.** Bottom half is **the agent that already read that data, presented so the trader knows *why* the system thinks what it thinks.** Click "View full prompt + call" → opens the exact system/user prompt and the structured JSON the LLM returned. This is the single most important feature for the fellowship submission — it is *auditable reasoning next to the evidence it reasoned about*.

The four panels:

- **Fundamentals** — raw: P/E, Fwd P/E, EV/EBITDA, P/B, P/S, FCF yield, dividend yield, next earnings, revenue growth YoY, net margin. Agent: `fundamentals_signal` from the journal.
- **Technicals** — raw: RSI(14), Bollinger %B, MACD, 20D realized vol, ATR(14), distance to 20D/50D/200D MAs. Agent: `technicals_signal`.
- **Sentiment** — raw (v1): news count last 24h, net sentiment score if we have it, analyst-rating mix (buys/holds/sells). Agent: `sentiment_signal`. Full news feed and Reddit/X volume come in Phase C.
- **Risk** — raw: current position in portfolio, unrealized P&L, correlation to SPY/QQQ/sector, portfolio drawdown state, sector concentration. Agent: `risk_verdict` with `vetoed / approved` and the reason.

### A.3 — Committee band

Reuse the existing `RecordView` component — this is already the strongest piece of the frontend. One change: add a `[Rerun on current snapshot]` button that calls `POST /api/analyze/{ticker}` and streams the new run inline.

### A.4 — Freshness badges

Every panel header shows a tiny timestamp chip: "quote 32s old," "fundamentals 3d old," "snapshot cached." Red if stale beyond a per-panel threshold. The freshness module (`argos/indicators/freshness.py`) is already there; just surface it.

**Reuse map.** Chart is new code (~400 LOC). Panels are each ~80 LOC and re-use `frontend/src/components/record/*`. Everything backend-side was built in Phase 0.

**Research angle preserved.** Two gains for the paper, no losses:

- *Auditability-as-UX.* The paper can show one screenshot where raw evidence sits next to agent reasoning next to final verdict next to historical calibration. That single image is worth a page of prose.
- *Override log foundations.* The `[I disagree]` button (implemented in Phase E but stubbed here as a no-op button) is a direct measurement surface — eventually we'll have a dataset of "when did a human disagree with the committee and who was right more often."

**Exit criteria.** Open `/ticker/NVDA` → see the full screen filled with live data + live agent verdicts in under 10s. Click a past decision dot → the journal drawer opens with the full prompt and structured output visible.

---

## 4. Phase B — event-driven firing (~2 weekends)

**Goal.** Stop running the pipeline on a calendar; start running it when something that would matter to a trader actually happens.

**Scope.**

### B.1 — Trigger library

New module `argos/triggers/` with four submodules, each exposing a `check(ticker, as_of) -> list[Trigger]`:

- `argos/triggers/scheduled.py` — fires N days before earnings, ex-div, Fed meeting, CPI print, OPEX. Data from the existing fundamentals store + a small Fed calendar JSON (pinned, not scraped).
- `argos/triggers/technical.py` — RSI(14) crosses 30 or 70, price breaks 20-day Donchian channel, Bollinger band squeeze followed by expansion, 20D volume > 2× 20D average, golden cross / death cross. All computable from `argos/indicators/engine.py` output.
- `argos/triggers/fundamental.py` — new 13F showing a tracked holder changed position > 20%, new Form 4 insider transaction above a size threshold. Deferred to Phase C when we have the data pipes.
- `argos/triggers/news.py` — material news tag fires. Deferred to Phase C.

### B.2 — The event loop

New script `scripts/event_loop.py`. Long-running process:

```
for ticker in watchlist:
    triggers = []
    for module in [scheduled, technical, fundamental, news]:
        triggers.extend(module.check(ticker, now()))
    if triggers:
        # pass trigger context into the edge gate as a new state field
        result = pipeline.invoke(ticker, now(), triggers=triggers)
        if result.final_decision.action in ("buy", "sell"):
            notify(ticker, result)
sleep(check_interval)
```

### B.3 — Edge gate reads triggers

One small change to `argos/nodes/edge_gate.py` and `prompts/edge_gate.md`: when triggers are present in state, the gate sees them in its prompt. This is subtle but important — the gate stops being *only* a cost-saving device and becomes the paper's **multi-scale selective-reasoning claim**: the event layer triggers at the watchlist scale, the gate triggers at the decision scale. Same thesis, two layers.

### B.4 — Live dashboard view

`frontend/src/views/Live.tsx` — the watchlist grid in Section 1. Each row refreshes every N seconds via polling (SSE is overkill for a grid). Row click → opens the per-ticker page from Phase A.

### B.5 — Notifications

Opt-in webhook / email on BUY and STRONG_BUY only. SMTP creds in `.env`; webhook URL in portfolio config.

**Research angle preserved — and, critically, strengthened.**

- A new ablation becomes available: **event_triggered vs calendar_triggered.** Same system, same universe, same date range — but one arm fires on events, the other fires weekly as today. The paper's selective-reasoning story goes from "we abstain intra-decision" to "we abstain intra-decision AND we only convene when something happens." That is a research contribution nobody else has quantified.
- Coverage measurements remain clean because we keep the weekly reference run.

**Exit criteria.** Set `WATCHLIST=NVDA,TSLA,ORCL,KO`, start `python scripts/event_loop.py`, leave it running. Within a trading day, at least one trigger fires on at least one ticker; the pipeline runs on that trigger; a notification lands; a new journal appears under `logs/decisions/live/`.

---

## 5. Phase C — ownership, insider, news (~2 weekends)

**Goal.** Fill the right half of the Sentiment panel and introduce a new **Ownership** panel. This is where the system stops being a chart-and-committee tool and becomes a full research desk.

**Scope.**

- **C.1** 13F ingestion from SEC EDGAR. Quarterly filings. New module `argos/data/thirteenf.py`. Cached locally. Expose "top 10 holders," "quarter-over-quarter delta," "new positions," "exited positions."
- **C.2** Form 4 insider transactions (also EDGAR, real-time via RSS). New module `argos/data/insider.py`. Expose latest N insider trades, with role (CEO/CFO/Director) and transaction size.
- **C.3** News feed — pick one free provider (Finnhub has a generous free tier) and build `argos/data/news.py`. Store last 24h per ticker with sentiment-tagged items. The existing Sentiment analyst gets a richer input and the UI gets a news column.
- **C.4** New **Ownership** panel on the per-ticker page. Raw: top 10 holders, % change, insider trades last 90 days, short interest %, days-to-cover. Agent: *doesn't need a new agent* — the existing Fundamentals analyst's prompt can grow a new section ("ownership signals"). Key_factors in the agent output will cite ownership changes when relevant.
- **C.5** Fundamental triggers (B.3) go live: "a tracked holder changed position >20%" and "insider Form 4 of size > $N" become real event sources.

**Research angle.** 13F / Form 4 data makes a new ablation possible: **ownership-aware vs ownership-blind.** Run the same universe with the Fundamentals prompt including or excluding the ownership block. Measure the delta in directional accuracy and in the Risk Manager's veto rate. If ownership data helps, the paper gets a clean table. If it doesn't, that's still a result.

**Exit criteria.** The Ownership panel on `/ticker/NVDA` shows the latest 13F deltas and the last 5 insider Form 4s, and the Fundamentals analyst's `key_factors` cites at least one ownership-related factor on at least one ticker.

---

## 6. Phase D — the paper's ablation tables (~2 weekends, runs in parallel with B/C)

**Goal.** Ship the measurements CLAUDE.md names as to-do. These don't need UI; they are scripts that produce markdown tables and plots for the paper.

**Scope.**

- **D.1** `scripts/compare_judge_ab.py` — judge_on vs judge_off.
- **D.2** `scripts/compare_debate_rounds.py` — 1 round vs 2 rounds vs 3 rounds.
- **D.3** `scripts/compare_kronos_ab.py` — kronos_live vs kronos_stub.
- **D.4** `scripts/compare_loo_analyst.py` — leave-one-analyst-out (three runs: no-fundamentals, no-sentiment, no-technicals).
- **D.5** Factor out `scripts/eval_nasdaq.py` into `argos/reports/eval.py` so every ablation script imports the same aggregation code.
- **D.6** `scripts/calibration.py` already exists — extend to produce per-ticker reliability diagrams and a selective-prediction AUC plot.

**Reuse map.** All six scripts are variations of `scripts/compare_gate_ab.py` which already exists. Each is ~80 LOC of orchestration + one env flip to disable the component.

**Research angle.** This is the paper's methods section.

**Exit criteria.** Every ablation named in CLAUDE.md produces a reproducible table in `reports/ablations/` with git SHA and sample size.

---

## 7. Phase E — trust features (~1 weekend, cheap but high-leverage)

**Goal.** The features that make a cautious reader (or reviewer, or user) decide to trust the system. These are all small; they collectively change the vibe of the project.

**Scope.**

- **E.1** Override log — the `[I disagree]` button from Phase A becomes real. Writes to `logs/overrides.jsonl`. New Analytics tab panel: "override win rate vs committee win rate over last N decisions."
- **E.2** Calibrated confidence inline on every live verdict. Loads the latest calibration curve at startup; applies it to every live decision; displays both raw and calibrated.
- **E.3** Cost meter in the frontend header. Pulls from `argos/token_usage.py` which already tracks per-decision tokens.
- **E.4** Stage badges. Every journal record gets a `stage ∈ {backtest, walk_forward, paper, live}` field. The UI renders a colored chip.
- **E.5** Data freshness badges wired to every panel (begun in A.4; fully wired here).
- **E.6** Abstention panel in Analytics: when did we HOLD, and why? Coverage rate per ticker, per month.

**Research angle.** Abstention panel + override log are genuinely novel measurements for the paper. "The system abstained on TSLA 94% of the time and the user overrode 3 of those abstentions; the user lost money on 2 of 3 overrides" is a fellowship-grade finding.

**Exit criteria.** Open any live verdict → see raw confidence, calibrated confidence, cost in tokens, stage badge, freshness chip, and an [I disagree] button that persists an override.

---

## 8. What phase to do next — my recommendation

**Do Phase 0, then Phase A.1 and A.2, in that order, before anything else.**

Reasoning:

- Phase 0 is small, invisible, and *everything else is blocked without it*. The pipeline needs to be callable as "analyze this ticker now" before any UI can meaningfully consume it. Half a weekend.
- Phase A.1 (chart with decision dots) is the **single highest-leverage artifact in the whole roadmap** for both the trader and the paper. For the trader, it's the one visualization that makes the system comprehensible in two seconds. For the paper, it's the figure that will go on page one. A reader opening the paper sees: "Here is NVDA in 2024; here are the 53 points at which the committee met; here is where it went silent; here is where it bet; here is what the market did next." Every other phase is easier to motivate once that figure exists.
- Phase A.2 (four two-layer panels: raw data + agent verdict) is the UX commitment that locks in the "agents are researchers for the trader" reframe from your last message. Shipping A.1 without A.2 would make the chart beautiful but still not solve the "what are the agents FOR" question.

After that, **Phase B (event-driven) before Phase C (ownership/news)**, because:

- Phase B is pure wiring — the event registry and alert script already exist in half-built form; we are gluing them to the graph, not inventing them.
- Phase B unlocks the second ablation the paper needs most (event_triggered vs calendar_triggered) and the new multi-scale selective-reasoning thesis.
- Phase C needs new data pipes (EDGAR, Finnhub) which add real-world rate-limit and caching work; defer until the surface is in place to show them.

Phase D can and should run *in parallel* with Phase B / Phase C — ablation scripts are independent and anyone (or any fork) can pick one up and run.

Phase E is dessert. Ship it last; every item is small, each one adds disproportionate trust.

### Smallest sensible first commit

If I had to name one branch to open next week, it would be:

```
git checkout -b phase-0-plumbing
```

...and it would ship exactly the five routes in Phase 0 and nothing else. ~300 LOC. One PR. Everything after that is read-only against those endpoints and is parallelizable across sessions.

---

## 9. What explicitly does *not* change

Rules of engagement, so no phase quietly erodes what makes the project paper-worthy:

- **`argos/graph.py` is not restructured.** The 8-node pipeline stays. Event triggers enter as a new state field consumed by the edge gate; they do not add or reorder nodes.
- **No prompt is rewritten without an A/B.** Every prompt change (e.g. the Fundamentals prompt growing an ownership block in C.4) ships alongside a `scripts/compare_prompt_vX_vY.py` ablation and a before/after row in `reports/ablations/`.
- **Pydantic schemas in `argos/schemas.py` are extended only, never narrowed.** Adding a field to `FinalDecision` is fine; removing one breaks historical journals.
- **Model IDs stay pinned.** CLAUDE.md is strict here and so are we.
- **Journals remain append-only JSONL.** No migrations to a database; the file-based trail is the paper's reproducibility story.
- **`logs/decisions/nasdaq_focus/` is the paper's reference run.** Nothing writes to it except the weekly reference backtest.

---

## 10. One table to pin at eye level

| Phase | Goal                              | Effort     | Ships to trader | Ships to paper |
|-------|-----------------------------------|------------|-----------------|----------------|
| 0     | API plumbing                      | 1 wknd     | nothing yet     | nothing yet    |
| A     | Chart + 4 panels + committee band | 2 wknds    | **everything**  | one hero figure|
| B     | Event-driven firing + live view   | 2 wknds    | watchlist alerts| new ablation   |
| C     | 13F + Form 4 + news               | 2 wknds    | ownership panel | new ablation   |
| D     | Ablation scripts                  | 2 wknds    | nothing         | **methods**    |
| E     | Trust features                    | 1 wknd     | polish          | 2 novel metrics|

Total: ~10 weekends to a shippable research desk. Phases A, B, C, E help the trader. Phases A, B, C, D, E help the paper. No phase helps only one side.

---

## 11. The one sentence to carry forward

**The agents are not being replaced; they are being *shown.*** Every raw number a trader sees is paired with the structured verdict of the agent that already read it. That is the whole reframe, and every phase on this roadmap is one more move toward making that pairing visible on a screen.
