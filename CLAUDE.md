# CLAUDE.md — project rules for Claude Code

This file is loaded automatically into every session. Rules here override default behavior. Keep it tight — one rule, one reason, one line of guidance.

## Backtest ticker selection — rotate, don't repeat

**Rule:** Do NOT default to the same ticker (especially **NVDA**) on every backtest run. Each new small-chunk backtest must use a **different, previously-untested ticker** unless the user explicitly says "continue on X".

**Why:** Running the same ticker over and over tells us nothing new. The fellowship paper needs *coverage across market regimes* — high-vol growth (NVDA/TSLA), legacy-tech-pivot (ORCL), low-vol staples (KO), and whatever we add next. Repeating NVDA just produces another "NVDA Sharpe = X" row we already have; it wastes tokens and burns Kronos time without expanding the evidence base.

**How to apply:**
1. Before proposing a backtest run, check `logs/decisions/<universe>/` for which tickers already have substantial coverage. If a ticker has ≥20 journalled dates under the current config (gated or baseline), **pick a different one next**.
2. Cycle through the universe in order: finish ticker N before starting ticker N+1, unless the user intervenes. Current NASDAQ universe order: **NVDA → TSLA → ORCL → KO** (see `data/universe/nasdaq_focus.json`).
3. If every ticker in the current universe has coverage, propose *adding* a new ticker (one that exercises a regime not yet covered) rather than re-running the ones we have.
4. When proposing a run, state the ticker and the reason explicitly: *"ORCL next — NVDA has 53 baseline + 6 gated journals, we have no gated data on ORCL yet."* Don't make the user ask why.

**When to break the rule:**
- The user says "re-run NVDA" / "continue on NVDA" — do what they said.
- A previous run on the ticker errored out before useful data landed (<5 clean journals) — resume it rather than skipping.
- A code change invalidated the previous ticker's journals and they need re-generation — say so first and get confirmation.

## No AI-attribution trailers on commits or PRs

**Rule:** Never add `Co-Authored-By: Claude` / `Generated with Claude Code` / any AI-attribution line to commits or PR descriptions in this repo.

**Why:** This project is being submitted to the Fuse-AI fellowship as the user's own work. AI-attribution trailers would flag it as not-the-user's-work and compromise the submission.

**How to apply:** When creating a commit or PR, omit the attribution footer the harness would otherwise append.

## Small-chunk backtests, never one big run

**Rule:** When running the Argos backtest pipeline, do a **single ticker × small date range** (10-20 dates) per run. Never queue a full 4-ticker × 53-date cold run in one shot.

**Why:** Kronos runs on free Colab with a rotating cloudflared URL and a ~4-5h session cap. A single 503 storm, idle preemption, or URL rotation mid-run wastes everything upstream. Small chunks mean the worst-case loss is one ticker's worth of tokens, not the whole research budget.

**How to apply:**
1. Default scope: **1 ticker, 10 weekly dates**, ~10-15 min wall time.
2. Scale up (20 → 53 dates) **only after** a 10-date run on that ticker completed cleanly without a 503 cascade or disconnect.
3. Always `curl $KRONOS_ENDPOINT_URL/health` before kicking off the run. If Kronos is dead, stop and ask the user for a fresh URL — do NOT run with stubbed forecasts silently.
4. Before any backtest launch, ask the user to confirm Kronos is up (keep-alive active, tunnel URL current).

## Cost discipline on LLM calls

**Rule:** Be explicit about expected token cost **before** triggering a multi-call run. Print the estimated call count and rough dollar cost; wait for user go-ahead when the estimate exceeds the user's declared per-run budget (default: skip the check if clearly under $1, surface it if above).

**Why:** The fellowship work has real (if small) monthly budgets. Several incidents of "I'll just kick this off in the background" turned into >30 min token burns.

**How to apply:** The existing `_print_plan()` in `scripts/run_nasdaq_backtest.py` already does this — use its estimate and surface it in chat before the run, not after.

---

## Research-first framing (not profit-first)

**Rule:** Every claim, chart, and table produced from this repo is framed as a *measurement*, not as trading advice or a performance brag. "Can a multi-agent LLM desk abstain from trading without losing alpha?" is a research question; "look at this Sharpe" is not the point.

**Why:** This is a Fuse-AI fellowship submission. Reviewers judge the *rigor of the measurement*, not the size of the returns. A tiny Sharpe with a clean ablation beats a big Sharpe with no methodology. The current research angles (see earlier chat):
  - **Selective reasoning** — the edge gate. When should the committee convene?
  - **Debate asymmetry under symmetric prompting** — `abs_gap` across tickers.
  - **Per-role contribution** — which agent earns its tokens?
  - **Epistemic humility** — TSLA refuses to trade. Feature, not defect.

**How to apply:**
1. When reporting results, lead with the *question answered*, then the metric, then the caveats. Never lead with dollar PnL.
2. Describe HOLD as *selective abstention*, not as "no trade." The paper's framing is that abstention is a valid output of a reasoning system — borrowed from the `learning-to-defer` literature.
3. Do not call anything "alpha" or "edge" without a confidence interval or at least an n and a baseline comparison.
4. Never A/B a change without a baseline arm already in `logs/decisions/<universe>/` to compare against.

## LLM output evaluation standards

**Rule:** Every LLM output in this system must be: (a) structured (Pydantic schema), (b) journalled with full prompt + verdict, (c) scored against at least two criteria beyond "did the trade make money."

**Why:** The research value of a multi-agent LLM system is auditability. If the paper's reviewer can't ask *why did the bull say 0.67 confidence on 2024-03-14*, the system is a black box and the paper is weak. Everything we build must support post-hoc interrogation.

**How to apply:**
1. **No free-text outputs.** Every agent uses `.with_structured_output(SchemaName)` with a Pydantic model that includes a `confidence: float 0..1` and a `reasoning: str` field at minimum.
2. **Journal the prompt.** The exact `system` + `user` string sent to each agent goes into `analyst_prompts` in the state and into the JSONL record. Never shorten it for storage.
3. **Score beyond PnL.** Every agent is evaluated on at least:
   - **Directional accuracy** at h=1/5/20 (does their call match the realized move?)
   - **Coverage** (how often do they abstain / produce "neutral"?)
   - **Calibration** (does confidence predict correctness? Reliability diagram is the paper's go-to plot.)
   - Agents with directional outputs also get a **Brier score** / log-loss if a probability is produced.
4. **Abstention is a valid output and scored favorably.** An analyst that says "neutral" on an unpredictable day is better than one that bets wrong with high confidence. Build evaluation that credits well-timed abstention (coverage-vs-accuracy curves, selective-prediction AUC).
5. **Confidence gaps are a signal.** The bull/bear `abs_gap` is a first-class metric, not a side column. Track it per ticker, per regime.
6. **Never silently retry.** If an LLM call fails and the fallback produces a different answer, journal both. The paper cares when the provider cascade changes outputs.

## Ablations are mandatory for claims

**Rule:** No performance claim gets reported without a matching **ablation table**. Adding a component must come with "what did the system do without it?"

**Why:** Multi-agent systems are infamous for not being measurable — every component "helps" in some vague way, but nobody quantifies. The paper's leverage is doing that quantification where others didn't.

**How to apply:** For each major component (edge gate, blind judge, debate, risk manager, Kronos forecast), maintain a *paired baseline* journal set with that component disabled or stubbed. Run the eval harness on both. The table — not the prose — is the deliverable.

Current required ablation runs:
  - **gate_on vs gate_off** — already scoped (scripts/compare_gate_ab.py)
  - **judge_on vs judge_off** — to-do
  - **debate_single_pass vs debate_multi_round** — to-do
  - **kronos_live vs kronos_stub** — to-do
  - **analysts_all_three vs drop_one** (leave-one-out) — to-do

## Reproducibility

**Rule:** Every journal record must carry enough metadata to rerun that exact decision — git SHA, pinned model IDs, cached response hash where applicable.

**Why:** Fellowship reviewers (and future-you) must be able to audit any decision by re-running it. "It was Gemini 3.5 Flash" is not reproducible; "gemini-3.5-flash on key_A, cache hash abc123" is.

**How to apply:**
1. `argos/journal.py` already captures the git SHA (`argos_version`) — keep that.
2. `argos/llm_cache.py` deterministically caches by (prompt, model) — do not disable without saying so.
3. Model IDs in `argos/model_router.py` are pinned (not `-latest`). Don't "upgrade" a pin without a documented reason in the commit.
4. When reporting a result, name the exact date range, cadence, universe file, and env toggles (`EDGE_GATE_ENABLED`, `MAX_DEBATE_ROUNDS`, `MARKET`) that produced it.

## Negative results are results

**Rule:** If an experiment shows the component made things *worse* or made *no difference*, write it up as-is. Do not quietly retry with different settings until you get the number you wanted.

**Why:** Selective reporting is the fastest way to make a fellowship submission untrustworthy. A negative result ("the edge gate skipped 40% of real trading days, recall=60%, Sharpe halved") is a research finding — it tells the reviewer what the limits are.

**How to apply:** When a result is ugly, say so in the summary table AND in the chat response. Don't rephrase to flatter the system. The research credit is in the measurement, not the outcome.

## Don't overclaim

**Rule:** Claims in the paper draft, decks, or README must match the evidence in the journals. No "Argos beats baseline" unless the eval table says so with a sample size, a confidence interval, and a named baseline.

**Why:** Overclaiming on a fellowship submission is worse than underclaiming.

**How to apply:** When drafting narrative around numbers, cite the file that produced them (`reports/gate_ab/summary.json`, etc.) and include the n for the stat. If sample size is tiny (n < 30), say "preliminary" in the narrative.
