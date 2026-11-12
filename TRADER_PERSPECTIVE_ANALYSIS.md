# A Trader's-Eye Analysis of Argos / The Multi-Agent Trading Research Desk

*Written 2026-11-09 after walking the repo end-to-end. No code was changed; this is a reading of what is here, where it falls short of what a working trader would actually ask of it, and what the project should become if the goal is a genuine **research desk** rather than a backtest-generator dressed up as one.*

---

## 1. What the project actually is right now (plain English)

Strip away the names (Argos, Fuse, the fellowship framing) and here is what the codebase is doing today.

For a single `(ticker, date)` pair — say `("NVDA", "2024-03-14")` — the system runs a seven-stop pipeline. The whole thing lives in `argos/graph.py` and the nodes are under `argos/nodes/`.

1. **Exit Manager** (`argos/nodes/exit_manager.py`) — If a position in that ticker is already open in the toy portfolio, it asks an LLM: "given what we now know, should we close this position today?" If yes, the pipeline short-circuits to a SELL and stops.
2. **Three analysts in parallel** — a Fundamentals agent, a Sentiment agent, a Technicals agent. Each one is a separate LLM call with its own prompt (see `prompts/fundamentals_analyst.md`, etc.) and a Pydantic schema (`argos/schemas.py` — `AnalystSignal` with `signal ∈ {bullish, bearish, neutral}`, `confidence ∈ [0,1]`, `key_factors`, `reasoning`). The Technicals analyst also attaches a raw indicator snapshot computed in `argos/indicators/engine.py`.
3. **Forecast node** — calls a Kronos time-series model (hosted on a Colab notebook, `argos/data/kronos_client.py`) to get 1-day / 1-week / 1-month directional forecasts.
4. **Edge Gate** (`argos/nodes/edge_gate.py`) — a cheap LLM call that reads the analyst signals + indicator snapshot and decides: *is today worth running the expensive debate?* If no, the pipeline short-circuits to HOLD. This is the "selective reasoning" angle the CLAUDE.md file leans on for the paper.
5. **Bull vs Bear debate** (`argos/nodes/debate.py`) — two LLM calls, N rounds (default 2). The Bull advocate argues the long case. The Bear rebuts. A transcript is kept.
6. **Blind Judge** (`argos/nodes/judge.py`) — a third LLM that reads the transcript with the side labels stripped and scores which side argued better.
7. **Risk Manager** — looks at portfolio concentration, correlation, drawdown state (`argos/risk/`) and either vetoes the trade or lets it through.
8. **Portfolio Manager** — the final call. Outputs `{action ∈ {buy, sell, hold}, confidence, position_size, rationale}`.

Every one of those calls is journalled as JSONL under `logs/decisions/<universe>/<ticker>/<ticker>_<date>.jsonl` with the exact prompt, the exact structured output, token counts, and a git SHA (`argos/journal.py`).

Three surface layers sit on top of that core:

- A **FastAPI server** (`server/`) that exposes `POST /api/runs`, live SSE streaming of the graph as it executes (`/api/runs/{id}/stream`), and read-only access to past journals.
- A **React frontend** (`frontend/src/`) with three views: `CommandDeck` (kick off a run and watch it unfold), `JournalExplorer` (browse past decisions), `Analytics` (aggregate stats).
- A **backtest harness** (`scripts/run_nasdaq_backtest.py`, `scripts/eval_nasdaq.py`) that loops the pipeline across 53 weekly dates × 4 tickers (NVDA, TSLA, ORCL, KO) and builds the summary tables in `reports/nasdaq_focus/`.

That is the whole system in one breath.

---

## 2. What you (the user) are actually asking

Reading your message carefully, three distinct questions are tangled together, and they deserve to be separated before we propose anything:

- **(Q1) What is the point of the backtest?** You know how to backtest a strategy. What confuses you is that *this* backtest doesn't tell a trader whether to buy TSLA tomorrow — so what is it for?
- **(Q2) Why is the system scheduled on a weekly cadence instead of reacting to events?** A trader doesn't wake up Monday and say "it's Monday, let me reconsider all my positions." A trader reacts to earnings, Fed meetings, Musk tweets, a 52-week-high breakout, a sector rotation. The pipeline should fire *when something happens*, not on a calendar.
- **(Q3) The deliverable surface doesn't look like a trader's screen.** A trader looking at this would not know where to put their eyes. There's no live chart with RSI/Bollinger overlaid. There's no "who owns this stock" panel. There's no "what happened today" news feed. The backtest summary tables are *researcher artifacts*, not trader artifacts.

All three are valid. Let's take them one at a time, because the answers aren't the same.

---

## 3. The backtest — what it is actually for (reframing Q1)

Your instinct — *"if I want to buy a trade I wouldn't need to backtest, I just need to run it now"* — is correct **for a trader using a strategy they already trust.** But the backtest here isn't serving that trader. It is serving a *very different* reader:

- **Someone deciding whether to trust the system on a new ticker.** You said it yourself, better than I could paraphrase it: *"suppose I want to buy TSLA, now I run backtest and the system never said BUY, so I know this system is not good for that ticker."* That is exactly the right mental model. The summary for NVDA says h=5 directional accuracy 82%, Sharpe 1.58. The summary for TSLA has empty cells (`—`) because the system refused to trade it even once. **The backtest is a diagnostic for the system, not a signal for the trade.**
- **The fellowship reviewer.** CLAUDE.md is explicit that this is a research submission, and the backtest is the measurement surface: ablations (gate on vs off), calibration plots, directional accuracy per analyst per horizon, debate-symmetry checks (`abs_gap`). That is a methods section, not trading advice.

So the backtest has two legitimate uses right now:

1. *Can this system be trusted on this ticker in this regime?* → look at the per-ticker table and the calibration plot.
2. *Does component X (gate, judge, debate) earn its tokens?* → look at the ablation table.

**What it is NOT for** (and should never be marketed as): a signal source. A trader looking at `reports/nasdaq_focus/summary.md` and buying NVDA because "it said Sharpe 1.58" is reading the table wrong. 53 weekly decisions on one calendar year is nowhere near enough sample to call anything a strategy.

### The fix — label the backtest honestly in the UI

Three concrete changes would resolve your confusion for every future reader:

- **Rename the tab.** Not "Backtest" — call it **"System Diagnostic"** or **"Trust Panel"**. One is what we're actually doing.
- **Lead every ticker page with the trust question, not the Sharpe.** *"On NVDA, across 53 weekly decisions in 2024, the system traded 11 times and was directionally right at h=5 on 82% of them. On TSLA, the system refused to trade — this is a feature if you believe in epistemic humility, but the implication is the system has nothing to say about TSLA and you should not use it there."*
- **Overlay decisions on the price chart.** You asked for this explicitly and you are right. A table of 53 decisions is unreadable; the same 53 decisions plotted as colored dots (green BUY, red SELL, grey HOLD, hollow dots for gate-skipped) on the TSLA/NVDA/ORCL/KO candle chart is readable in two seconds. Every dot should be clickable → opens the journal record in a side drawer with the full prompt, the three analyst takes, the debate transcript, the risk veto reason. **That's the research-desk aesthetic: you trust the system because you can audit any single decision.**

---

## 4. Event-driven, not calendar-driven (Q2)

The current setup runs every Monday (weekly stride on `scripts/run_nasdaq_backtest.py`). That's a backtest artifact — the paper needs evenly-spaced observations to compute coverage ratios and average Sharpe. For an actual research desk, that's the wrong firing rule. A trader never thinks on a weekly cadence; a trader thinks when **something changes.**

The infrastructure for event-driven firing is *already in the repo*, half-built, scattered across three places, and nobody has wired them to the LangGraph pipeline.

- `argos/events/registry.py` already normalizes four event sources (dividends, earnings, SEBON promoter filings, broker-cluster activity) into a unified `EventRecord`. It was built for the NEPSE side, but the shape is market-agnostic.
- `scripts/event_alerts.py` already demonstrates the "80% oracle" pattern: iterate the universe, check `events_in_last(symbol, as_of, n_bars)`, fire a calibrated probability only when an event is fresh, be silent otherwise. This is the right mental model.
- `argos/data/announcement_reaction.py` already has logic to label an event with its realized 5/10/20-day price reaction so you can train on it.

What's missing is the plumbing. The LangGraph pipeline (`argos/graph.py`) starts from `START → exit_manager → analyst_router`. There is no event-trigger node, no scheduler, no watchlist listener. A trader running this live has no way to say *"wake me when NVDA has earnings in the next two days, OR when the 20-day realized vol spikes above its 90th percentile, OR when RSI(14) crosses 70 on any of my 15 watchlist names."*

### What event-driven should look like

A real event loop for this project needs three ingredients:

1. **A trigger layer** (new — doesn't exist yet). A long-running process that watches each ticker in the user's watchlist and emits an event when any of these fire:
   - **Scheduled events** — earnings announcement within N days, ex-dividend date, Fed meeting, CPI release, OPEX week.
   - **Technical triggers** — RSI(14) crosses 30 or 70, price breaks the 20-day Donchian channel, Bollinger band squeeze followed by expansion, 50-day MA / 200-day MA golden-cross or death-cross, unusual volume (today's volume > 2× the 20-day average).
   - **Fundamental triggers** — a new 13F filing from a tracked holder, an insider Form 4 filing, a 10-K/10-Q filing that moves a tracked metric by > X%.
   - **Sentiment / news triggers** — a news item tagged as material (M&A, guidance change, lawsuit, product recall), a Reddit / X volume spike on the ticker's cashtag, an analyst re-rating from one of the sell-side desks you track.
2. **A gating layer.** Once an event fires, we don't want to run the full 8-call LLM pipeline on every tiny tick — that's $$ wasted and the *point* of the edge gate (`argos/nodes/edge_gate.py`) is already to triage. So the event should enter the gate first, carrying its magnitude and type, and the gate decides: *proceed to full debate, or just log-and-silent?*
3. **A notification layer.** When the pipeline produces a BUY or STRONG BUY, the user hears about it — webhook, email, desktop push, whatever. A decision journal is nice for audit; a notification is what a trader needs *in the moment*.

### How to wire this without blowing up the existing paper

Keep the weekly-cadence backtest exactly as it is — that's the measurement surface for the paper, and ablation tables need it to be reproducible. Add a **parallel live mode** that reuses the same graph, same prompts, same journals, but is triggered by events instead of by a loop. The journals will interleave cleanly because they're keyed by `(ticker, as_of_date, run_id)`. The paper's ablations stay clean; the trader surface gets what it needs.

One concrete suggestion: a new `scripts/event_loop.py` that does what `scripts/event_alerts.py` already does *(scan for events in the last N bars)* but, on every fire, invokes the LangGraph pipeline and writes the journal under a new namespace: `logs/decisions/live/<ticker>/`. The React frontend grows a fourth view — **Live** — that is just the Analytics view filtered to today's events.

---

## 5. What a trader's screen actually looks like (Q3)

This is the biggest gap in the current product, and the one the user is most agitated about. Let me describe what a working equity trader with a Bloomberg or a TradingView Pro sees on their monitor when they open a ticker, because the framing "would a trader even know what to do with this?" is exactly the right question.

A trader opening TSLA at 9:25 AM wants, on a single screen:

- **A price chart** — candles, user-selectable timeframe (1-minute, 5-minute, 1-hour, 1-day), with overlays they can toggle. The overlays a real equity trader uses day-in-day-out:
  - **Moving averages** — 20 / 50 / 200 day (and 9-EMA for scalpers).
  - **Bollinger Bands** — 20-day, 2σ (band squeeze is a signal of its own).
  - **RSI(14)** — in a sub-pane, with 30/70 lines.
  - **MACD** — in a sub-pane.
  - **VWAP** — intraday anchor.
  - **Volume** — colored by direction, with the 20-day average plotted as a horizontal reference.
- **Levels** — 52-week high/low, prior day's close, pre-market high/low, overnight ES futures anchor.
- **Fundamental snapshot** — P/E (trailing + forward), EV/EBITDA, P/B, P/S, forward revenue growth, net margin, FCF yield, debt/equity, next earnings date (± days), dividend yield + ex-date.
- **Ownership panel** — top 10 institutional holders (from 13F), % change in holdings last quarter, short interest % of float, days-to-cover, insider ownership %, recent Form 4 insider buy/sell list (the "latest big event of who bought and who didn't" line you wrote is literally this).
- **News / events ticker** — latest 10 items with time stamps and sentiment tags.
- **Analyst coverage** — street mean price target, high / low target, number of buys/holds/sells, recent rating changes.
- **Options surface** (if applicable) — IV rank, 30-day IV vs 30-day HV, put/call ratio, max-pain, biggest gamma strikes.
- **Correlations** — correlation to SPY, QQQ, VIX, its sector ETF, and its two closest peer tickers. A trader wants to know: is this move idiosyncratic or is the whole sector moving?

Now — how much of that does the current project show?

| Element | In the repo right now? | Where |
|---|---|---|
| Price chart | **No.** OHLCV is fetched (`argos/data/ohlcv_loader.py`) but only the summary is passed to prompts. No chart in the UI. | — |
| MA / Bollinger / RSI / MACD | **Computed, not shown.** `argos/indicators/engine.py` builds a full snapshot but it goes into the LLM prompt, not the UI. | `argos/indicators/engine.py` |
| Fundamentals (P/E etc.) | **Partially.** `argos/fundamentals/scoring.py` + `scripts/fetch_fundamentals_gemini.py` fetch some fundamentals, but the Fundamentals analyst consumes them in text form; no fundamentals panel in the UI. | — |
| Ownership / 13F / insider Form 4 | **No.** | — |
| News / events ticker | **Partial.** The sentiment analyst reads some text but there is no news surface for the user. | — |
| Analyst coverage | **No.** | — |
| Options surface | **No.** | — |
| Correlations | **Partial.** `argos/risk/correlation.py` computes it for the risk manager but doesn't display it. | — |

Translation: the system has done the hard half — fetch data, compute indicators, reason over them — and skipped the half that would make a trader take it seriously, which is **showing** all of this in one place next to the committee's decision. Right now the committee says "buy NVDA with 0.72 confidence" and the trader has to go to TradingView in another tab to see the chart, has to go to Yahoo in a third tab to see the P/E, has to go to WhaleWisdom in a fourth tab to see who's holding. Why would anyone bother? The whole value of a *research desk* is **one surface**.

### The screen as it should be

A single ticker page with three horizontal bands:

**Band 1 — the chart.** Price with user-selectable overlays. On the same chart, past Argos decisions plotted as colored dots. Dot color = action. Dot opacity = confidence. Dot shape = gate-skipped vs full-pipeline. Click a dot → the journal record slides in from the right.

**Band 2 — the panels.** Four columns, equal width:
- Fundamentals snapshot (static, updates on earnings).
- Ownership & flow (13F deltas, latest insider Form 4s, short interest, recent unusual options activity).
- News & events (last 10 items, highlighted if they fired an Argos trigger).
- Peers & correlations (SPY/QQQ/sector correlations, 1-month price chart of the two nearest peers for visual sanity check).

**Band 3 — the committee.** The current frontend's `RecordView` already does this part well. Three analysts, Bull / Bear transcript, Judge, Risk Manager, Final decision. Keep it — this is the project's crown jewel. The only change: make it live-editable. A trader should be able to re-run the committee *right now* on the current snapshot, not just read a 2024 journal.

### The dashboard view (across all watchlist tickers)

One row per ticker, in a dense grid. Each row:

`[ticker] [sparkline 1D] [price Δ] [IV rank] [RSI14] [gate state: AWAKE/SILENT] [last committee verdict (colored dot)] [next scheduled event] [notification bell]`

Sortable by any column. Filterable by "fired an alert in last 24h" / "has earnings this week" / "oversold on RSI." *This* is a trader's screen.

---

## 6. Things traders expect that nobody in the AI-trading-agent space tends to build (researched gaps)

Beyond what you explicitly asked for, here are the things I've seen trip up every multi-agent-trading repo I've looked at, and all of them apply to this one.

### 6.1 Position-level, not signal-level, reasoning

The current pipeline thinks in isolated `(ticker, date)` tuples. A real trader thinks in **positions** — this is my 300-share NVDA long from Feb 12 at an average of $632, stop at $598, target 1 at $720, target 2 at $780, current P&L +$14,200. When the committee says "sell NVDA," the trader's first question is "sell all of it, or trim 100 shares to lock in?"

The Exit Manager (`argos/nodes/exit_manager.py`) is a step toward this, but it is binary (close / don't close). A trader wants partial exits, scaling into winners, trailing stops that move with the chart. None of that language is in the prompts right now.

**Recommendation.** Add a `PositionContext` block to every pipeline run when the ticker is already held: entry price, days held, unrealized P&L, distance to stop, distance to target, how big the position is relative to the portfolio. Then let the Portfolio Manager output structured actions beyond buy/sell/hold — `scale_in`, `trim_25`, `trim_50`, `exit_all`, `move_stop_to_breakeven`. The journals already have the shape for this; the schemas need one new enum.

### 6.2 Horizon-explicit calls

The current schema says `signal ∈ {bullish, bearish, neutral}`. A trader wants to know: bullish **for how long**? Swing (3–10 days)? Position (weeks)? Long-term (months)? The system actually *does* produce multi-horizon forecasts (`multi_horizon_forecast`) but the final decision collapses them into a single `action`. That is throwing away information a trader would use.

**Recommendation.** The final decision should carry an explicit `horizon_days` and a `conviction_decay` — "bullish for 5 days, flat after." This is also a cleaner thing to backtest against because it tells the eval harness which realized return to compare to.

### 6.3 Regime awareness

The paper framing talks about "coverage across market regimes" (high-vol growth, legacy-tech, low-vol staples). That's the ticker-level answer. The trader-level answer is: *what regime is the broad market in right now?* A trader interprets every signal through the current regime. The same RSI(70) on NVDA means something very different in a QQQ uptrend vs. during a correction.

The repo has `argos/data/macro_context.py` — which, as far as I could tell from the directory listing, is there but not wired into the agent prompts.

**Recommendation.** Every pipeline run should prepend a one-paragraph macro context to every analyst prompt: *"As of 2026-11-09, SPX is at $5,840, 20-day realized vol 11.3% (60th pct), VIX 14.2, 10Y yield 4.21%, DXY 103.1. Fed next meeting 2026-12-10 (expected cut: 15bp)."* The LLM will reason better when it knows what weather it is in.

### 6.4 Confidence calibration should be user-visible

The paper's calibration work (`argos/learn/calibration.py`, `reports/calibration/`) is one of the most valuable things in the repo for a trader — it tells them "when the system says 0.7 confidence, it's historically been right 68% of the time." But right now this lives in the research report. A trader looking at a live committee verdict wants the calibration *there, inline*: "Final decision: BUY with 0.72 confidence → calibrated to 0.65 historical win rate at h=5."

**Recommendation.** Load the latest calibration curve into the serving layer and apply it on the fly to every live verdict. Show both the raw LLM confidence and the calibrated probability.

### 6.5 The abstain decision as a first-class output

CLAUDE.md is explicit that this is a research angle: *"TSLA refuses to trade — feature, not defect."* The paper framing is beautiful. The trader framing needs to match. Right now the UI probably renders a HOLD the same way as a non-event; a trader should see **abstention as a decision**, with its own icon, its own reasoning panel ("why didn't we trade today?"), and its own alert channel ("the system considered TSLA this week and declined to trade it — here's why"). Silence is not nothing; silence is information.

### 6.6 "What if I disagree" — the trader's override

A trader who uses an AI system they didn't personally train will *eventually* disagree with it. The system that doesn't let them record and track their disagreement loses their trust forever. Give the user an **override log**: a button on every committee verdict that says *"I'm doing the opposite — here's why."* Store the override, store their reason, score it later. Over a few months the user will see — in cold numbers — whether their gut outperforms the committee or not. That is *research on the user*, and it's usually the most compelling result a system like this produces.

### 6.7 Explanation depth — tune the verbosity

A quant wants the full debate transcript. A manager wants a one-line summary. A compliance person wants the audit trail. The pipeline produces all three — the structured output covers the one-liner, the debate covers the depth, the journals cover the audit. The UI should expose a verbosity toggle: **summary / briefing / full transcript / audit**. Right now `RecordView` shows one layer.

### 6.8 Live cost meter

The CLAUDE.md file is pretty strict about cost discipline. Right now the user has to go look at the journals to see tokens spent. Put a running **cost meter** on the top-right of every page: "session cost so far: $0.43 • today: $1.80 • this month: $18.40." This is a tiny feature with an outsized effect — it earns trust immediately because the system is being honest about what it costs.

### 6.9 Data freshness badges

A chart from yesterday with a verdict from today is a bug waiting to embarrass the project. Every data pane should carry a **freshness badge** — "quote 32s old," "fundamentals 3d old (next refresh on earnings 11-14)," "news 4m old." If the committee runs on stale data, the badge turns red. The freshness module (`argos/indicators/freshness.py`) is already in the repo; it just needs to surface.

### 6.10 Backtest → walk-forward → paper → live, as four visible stages

Right now the only distinction is "backtest journal" vs "live journal." A real research desk has four states and the user should see which one any given result lives in:

- **Backtest** — in-sample historical replay. Lowest trust.
- **Walk-forward** — the model was trained / tuned on data up to date T, then evaluated on T+1 to T+K. Medium trust.
- **Paper** — live-data, live-prompt, no money moving. High trust.
- **Live** — real money. Highest trust (and the project doesn't need to ever ship this to have value — most research desks never do).

Badge every verdict with its stage. A BUY from a backtest is not the same thing as a BUY from live paper-trading, and conflating them is how people lose money.

---

## 7. The research-vs-trader tension (and how to resolve it)

The user is pulling the project in two directions and both of them are valid:

- **"Make it more research."** (CLAUDE.md framing: structured outputs, ablations, calibration, reproducibility.)
- **"Make it more trader."** (This message: live chart, event-driven, fundamentals panel, overrides.)

These seem to pull apart but they *don't*, because they share the same primitive: **the auditable, structured, journalled decision.** The research version of that primitive is "aggregate 53 of them into a calibration curve." The trader version is "show me this one, right now, next to the chart." Same data; two readings.

The project should commit to being **a research desk that a trader can also use**, not an *either-or*. The practical form of that commitment is:

1. The LangGraph pipeline and the journal are the core. They do not change.
2. Everything else is a *view* over the journal — backtest tables are one view, live chart with decision dots is another, event-alert webhook is a third, calibration report is a fourth.
3. The paper gets written from the first and fourth views. The trader surface is the second and third. Nothing is thrown away; the researcher and the trader are reading the same substrate with different lenses.

This also solves a tension CLAUDE.md gestures at but doesn't fully resolve: *"research-first framing (not profit-first)."* The paper does not need to prove the system makes money. It needs to prove the system *measures itself honestly.* A trader-facing UI with an override log, a freshness badge, an abstain-rate counter, a cost meter, and a calibration-adjusted confidence is itself a research contribution — it is literally a UI for making a multi-agent system auditable. Nobody else has built that well.

---

## 8. What I would actually build next — a phased proposal

In priority order, with cost / benefit roughly estimated.

### Phase A (1–2 weekends) — close the obvious UI gap

- **A.1** Add a candle chart to the `JournalExplorer` view (lightweight-charts is fine) and overlay past decision dots on it. Green/red/grey by action, opacity by confidence, hollow for gate-skipped. Click → open the existing RecordView in a side drawer.
- **A.2** Add an indicator sub-pane with RSI(14), MACD, Bollinger — reusing values already computed in `argos/indicators/engine.py`. Don't recompute; expose the existing snapshot via a new API route.
- **A.3** Add a fundamentals panel next to the chart. Pull from the data the Fundamentals analyst already ingests — P/E, forward P/E, EV/EBITDA, P/B, next earnings date, dividend yield. No new data sources needed for v1.
- **A.4** Add a freshness badge to every pane.

This phase makes the project *look like a research desk* instead of a backtest report. None of it touches the graph or the paper's numbers.

### Phase B (2–3 weekends) — event-driven firing

- **B.1** Build `scripts/event_loop.py`. Reuses `argos.events.registry` + adds technical triggers (RSI cross, Donchian break, volume spike) and scheduled triggers (earnings within N days).
- **B.2** Add a **Live** view to the frontend. Dashboard grid of watchlist tickers with per-ticker status (sleeping / awake / fired). Each ticker clicks through to Phase A's chart view, but scoped to live mode.
- **B.3** Notification channel (webhook / email) when a BUY or STRONG_BUY fires. Keep it opt-in; a research project doesn't need to be sending alerts to anyone.

### Phase C (longer) — ownership, insider, and news layers

These need actual new data sources and some care about free-tier rate limits.

- **C.1** 13F ingestion (the SEC's quarterly institutional-holdings filings). Free from SEC EDGAR, parseable with `sec-edgar-downloader`.
- **C.2** Form 4 (insider transactions). Also free from EDGAR, real-time via RSS.
- **C.3** A news feed — Finnhub / Alpha Vantage / Benzinga have free tiers; pick one and tag items against the watchlist.
- **C.4** Short interest and options unusual-activity — these are harder on a free tier; defer.

### Phase D (paper-facing) — the measurements the paper actually needs

The paper deliverables already named in CLAUDE.md:

- gate_on vs gate_off ablation — *already scoped*.
- judge_on vs judge_off ablation.
- debate_single_pass vs multi-round.
- kronos_live vs kronos_stub.
- leave-one-analyst-out.
- calibration reliability diagrams per ticker, per horizon.

Each of these is a `scripts/compare_<x>_ab.py` style script that walks two journal sets and produces a markdown table + a plot. The eval harness `scripts/eval_nasdaq.py` is 80% of the way to being a library the ablation scripts can call. Factor it out.

### Phase E — the trust features

- **E.1** Override log (user disagrees with committee; record and score over time).
- **E.2** Calibrated confidence inline on every live verdict.
- **E.3** Cost meter.
- **E.4** Stage badges (backtest / walk-forward / paper / live).

Phase E is cheap in engineering and expensive in trust-per-line-of-code — it's the best cost/benefit of everything on this list.

---

## 9. Specific things to stop doing

A few things the current structure does that are worth explicitly walking back.

- **Stop defaulting the Command Deck form to `{ticker: "NVDA", date: "2024-01-02"}`.** The default landing experience should be either the user's last run or today's live state, never a stale backtest date — a new user will think the system is frozen in 2024.
- **Stop treating the summary.md table as the headline artifact.** A four-row directional-accuracy table with one row entirely empty (TSLA) is a weak first impression. Replace the headline with a chart-with-decisions image for one ticker and a one-paragraph "what this means."
- **Stop calling the weekly backtest "the backtest."** It's one cadence of one slice of one year; call it what it is: *"the weekly 2024 reference run."* That way a reader knows another cadence / year / universe is possible.
- **Stop journalling without a rendered summary.** Every pipeline invocation should produce not only the JSONL record but a one-screen PDF/PNG summary of the decision (chart + committee + verdict) that can be shared on its own. This is the artifact a trader screenshots and sends to a friend. It is also the artifact the paper's reviewer will quote.

---

## 10. Pulling it all together — one-sentence answer to each of your questions

- *"What's the point of the backtest?"* → It's a **trust diagnostic**, not a signal source. The right summary isn't Sharpe; it's "on this ticker, did the system know when to shut up, and when it spoke was it calibrated?"
- *"Why isn't it event-based?"* → The paper needed a fixed cadence for coverage math; the live surface should fire on events. The event registry is already half-built; wire it to the graph and add a Live view.
- *"Where's the chart with indicators and the fundamentals panel?"* → Not yet built. The data is already computed; it just hasn't surfaced. Phase A above is the smallest change that removes this complaint entirely.
- *"Where's the ownership / insider / 'who bought and who didn't' layer?"* → Phase C. Needs new data sources, but all free and all off EDGAR.
- *"Where are the overlay decisions on the backtest chart?"* → A.1. Highest leverage item on the whole list. The journals carry the exact timestamps and verdicts; it's twenty lines of charting code.
- *"Would a trader even look at this?"* → **In its current form, no.** After Phase A, maybe. After Phase B, probably. After Phase C and E, yes — and they'd find it unusually honest compared to the rest of the market, because this is the only system I've seen that treats *abstaining from a trade* as a first-class decision with a rationale trail. That's the paper's thesis and it's also the trader-facing product's differentiator. Those are the same thing. Lean into it.

---

## 11. Closing — the single most important reframing

The confusion in the user's message is, I think, this: the project was pitched to itself as **a trading system** when it should have been pitched to itself as **a reasoning system about trading.** A trading system lives or dies on Sharpe. A reasoning system lives or dies on whether its reasoning is auditable, calibrated, and honest about when it doesn't know. The second one is what this project is actually good at; it just hasn't admitted it to itself yet.

Every design decision pulls into focus once that reframe is accepted:

- The backtest is not for picking trades — it's the harness that lets us measure the reasoning system against ground truth. **Keep it, but demote it from the headline.**
- The live surface is not a signal generator — it's a research desk that shows a trader the reasoning *as it forms*, in real time, with indicators and fundamentals in the same view. **Build it, because the paper's thesis is strongest when a reader can watch the system work.**
- The event-driven layer is not a trading engine — it's the mechanism that triggers the reasoning only when reasoning is warranted. The edge gate (the paper's selective-reasoning angle) is literally this idea pushed down one level — the event layer is the edge gate at the *watchlist* level, not the *decision* level. They are the same research angle at two scales. **Build both, publish both.**
- The ownership / insider / news panels are not decoration — they are the evidence a human reasoner would actually look at, and showing them next to the committee's reasoning is exactly how you let a reviewer audit whether the LLMs are reasoning over the right inputs or hallucinating.

If I had to leave the user with one sentence it would be this: **you don't have a weak trading system here, you have a strong reasoning system with a weak trader-facing skin.** Replace the skin and the project becomes the thing it was always trying to be.
