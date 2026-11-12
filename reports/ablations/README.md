# Ablations — the paper's methods tables

The scripts under `scripts/compare_*.py` each take two journal roots and
produce `summary.md` + `summary.json` + `per_decision.csv` in a dedicated
sub-directory of this folder. Every ablation answers the same shape of
question: *does toggling component X preserve decision quality while
changing cost in the expected direction?* The answers are the paper's
methods section.

**Reference run.** Every ablation treats the paper's weekly reference run
at `logs/decisions/nasdaq_focus/` as either arm A or arm B (noted per
table). You never regenerate the reference run; you only generate the
*other* arm by re-running `scripts/run_nasdaq_backtest.py` with the right
env flag or CLI switch, writing to a parallel `--output-root`.

**Cost gate.** Every treatment arm is a fresh backtest. Rough cost on
NVDA+TSLA+ORCL+KO × 53 weekly dates: ~$25–40 on a cold run. Budget
accordingly. Running the scripts themselves costs $0 — they only read
journals. Order the generation runs from cheapest-to-produce to most
expensive so an interruption still gives you *some* result.

---

## 1. Gate on vs gate off — `compare_gate_ab.py`

**Status.** Already built and the paper's main table. Not a Phase D
deliverable; listed here so the index is complete.

- **A = baseline**  `logs/decisions/nasdaq_focus/`  (judge off at pipeline
  level is irrelevant here; this is just full pipeline without the gate)
- **B = gated**    `logs/decisions/nasdaq_focus_gated/`
- **Headline.** Gate preserves X% of baseline trade days at Y% token cost.

Produce:
```
EDGE_GATE_ENABLED=1 python scripts/run_nasdaq_backtest.py \
    --output-root logs/decisions/nasdaq_focus_gated
```

Report lands at `reports/gate_ab/`.

---

## 2. Judge on vs judge off — `compare_judge_ab.py`

- **A = judge_on**  `logs/decisions/nasdaq_focus/`  (reference run)
- **B = judge_off** `logs/decisions/nasdaq_focus_judge_off/`
- **Headline claim to test.** The blind judge changes the PM's final call
  rarely enough that we could drop it and keep accuracy.

Produce:
```
JUDGE_ENABLED=0 python scripts/run_nasdaq_backtest.py \
    --output-root logs/decisions/nasdaq_focus_judge_off
```

Report lands at `reports/ablations/judge_ab/`.

---

## 3. Debate depth — `compare_debate_rounds.py`

- **A = rounds_1**  `logs/decisions/nasdaq_focus_rounds1/`
- **B = rounds_2**  `logs/decisions/nasdaq_focus/`  (reference run)
- **Headline claim to test.** The second round of bull vs bear rarely
  flips the PM's call; we could cap the debate at one pass.

Produce:
```
MAX_DEBATE_ROUNDS=1 python scripts/run_nasdaq_backtest.py \
    --output-root logs/decisions/nasdaq_focus_rounds1
```

Report lands at `reports/ablations/debate_rounds/`.

---

## 4. Kronos live vs stub — `compare_kronos_ab.py`

- **A = kronos_live** `logs/decisions/nasdaq_focus/`  (reference run — assumes
  Kronos was reachable during generation; check the run logs)
- **B = kronos_stub** `logs/decisions/nasdaq_focus_kronos_stub/`
- **Headline claim to test.** The Colab-hosted Kronos forecast is not
  moving the committee's call often enough to justify the ops cost on
  this universe.

Produce:
```
KRONOS_ENABLED=0 python scripts/run_nasdaq_backtest.py \
    --output-root logs/decisions/nasdaq_focus_kronos_stub
```

Report lands at `reports/ablations/kronos_ab/`.

---

## 5. Leave-one-analyst-out — `compare_loo_analyst.py`

- **A = all_three**           `logs/decisions/nasdaq_focus/`
- **B = drop_fundamentals**   `logs/decisions/nasdaq_focus_drop_fundamentals/`
- **B = drop_sentiment**      `logs/decisions/nasdaq_focus_drop_sentiment/`
- **B = drop_technicals**     `logs/decisions/nasdaq_focus_drop_technicals/`
- **Headline claim to test.** Only N of the three analysts measurably
  change the PM's call. The rest are decorative.

Produce (one per dropped analyst):
```
for a in fundamentals sentiment technicals; do
    DROP_ANALYST=$a python scripts/run_nasdaq_backtest.py \
        --output-root logs/decisions/nasdaq_focus_drop_$a
done
```

The script runs **all three pairs** in one invocation. Sub-reports:
`reports/ablations/loo_analyst/{fundamentals,sentiment,technicals}/summary.md`
plus a combined `summary.md` at the folder root.

---

## 6. Gate blind vs gate-with-triggers — `compare_gate_with_triggers.py`

Phase D's bridge back to Phase B. The edge gate currently does not read
the triggers from `argos/triggers/`. This ablation gates the prompt
change that would wire them in.

- **A = gate_blind**      `logs/decisions/nasdaq_focus_gated/`  (same as
  the gated arm of ablation 1)
- **B = gate_triggers**   `logs/decisions/nasdaq_focus_gated_triggers/`
- **Headline claim to test.** Giving the gate visibility into event
  triggers raises recall-vs-baseline (fewer suppressed trade days)
  without raising cost meaningfully.

Produce:
```
# The prompt change (prompts/edge_gate.md gaining a triggers section)
# ships in the SAME PR as this ablation. Both arms assume that PR's
# prompt + the GATE_READS_TRIGGERS env flag.
GATE_READS_TRIGGERS=1 python scripts/run_nasdaq_backtest.py \
    --output-root logs/decisions/nasdaq_focus_gated_triggers
```

Report lands at `reports/ablations/gate_with_triggers/`. **Merge rule:
if recall doesn't move at least +2 pp, the prompt change doesn't ship.**

---

## How each script is structured

Every script is a thin wrapper over `argos/reports/ablation.py`. The
shared harness provides:

- `analyze_arm(arm_name, journal_root, ticker, extras_fn=…) → ArmStats` —
  tokens, decision mix, directional accuracy at h=1/5/20, realized
  Sharpe / hit rate / max DD. `extras_fn(records) → dict` lets each
  script surface arm-specific metrics (judge agreement rate, mean
  rounds completed, gate skip rate, …) without touching the harness.
- `compute_pair(tickers, a_root, b_root, …) → list[PairResult]` — runs
  `analyze_arm` for both arms and computes the trade-day recall A → B.
- `render_pair_markdown(…)` — the standard paper-ready table set: cost,
  decision mix, directional accuracy, realized P&L, recall, arm-specific
  extras.
- `write_outputs(...)` — `summary.md` + `summary.json` + `per_decision.csv`.

If you need a new ablation, copy any of the five scripts and change
(a) the arm names, (b) the arm descriptions, (c) the `extras_fn` that
mines arm-specific metrics out of the records. ~60 LOC of new code.
