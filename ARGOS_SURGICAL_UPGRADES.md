# Argos — Surgical Upgrades (What The 44-Date ADBL Run Actually Told Us)

> Not another menu. This is an opinionated prescription based on the 44-date
> live-fire evidence. Every item names a **specific broken behavior** we
> observed, an **expected quantifiable lift**, and a concrete build
> instruction. Ordered so that #1 unblocks #2 unblocks #3.
>
> The old `ARGOS_IMPROVEMENTS.md` menu still stands as a reference, but if
> you can only do 12 things, do these.

---

## The five things the run actually revealed

1. **The machine is paralyzed.** 38/44 = **86% HOLD rate**, 6 directional bets across 44 trading days ≈ 1 bet per 7 days. A trading system that doesn't trade isn't trading — it's a fancy chart viewer.
2. **The Bear advocate is systematically more confident than the Bull** across EVERY single date (Bear 0.42-0.85 vs Bull 0.18-0.65). This is not the market — this is the OpenRouter Nemotron model having a bearish prior baked in. **Argos has a model-bias problem, not a market-reading problem.**
3. **Kronos direction is often right; magnitude is noise.** Predicted −12.9%, realized −0.9% (right direction, magnitude off by 14×). Predicted +3.2%, realized −0.23% (wrong sign, small magnitude).
4. **The 6 directional bets used tiny position sizes (0.03-0.12).** Even at 55% hit rate, size 0.05 × ±0.5% avg move = ~0.03% expected per trade. **We're playing for pennies and it shows in the P&L (−0.08%).** Kelly sizing would multiply this 5-10×.
5. **The machine has no exit logic.** BUY/SELL come out as one-shot signals. Real strategies live and die on WHEN TO EXIT — stops, trailing stops, time-decay. Argos plans none of it.

The upgrades below attack these in order.

---

## Upgrade 1 — Fix the Bear-bias asymmetry (**biggest single lift**)

**What we observed:** Bear conf > Bull conf on 43/44 dates. Bear averaged 0.61, Bull averaged 0.29. Realized returns for the same dates averaged **+0.03%** — mildly bullish reality vs strongly bearish machine. The gap is coming from a **model prior**, not the evidence.

**Root cause diagnosis (concrete):**
- Bull uses `groq/openai/gpt-oss-20b` (20B, likes to hedge, defers to skepticism)
- Bear uses `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` (550B, trained heavily on adversarial-response tasks — biased toward "here's why this is wrong")

**Fix (2 hours):**
1. **Same-model debate**: put Bull and Bear on the SAME model, different prompts. Any leftover asymmetry is prompt-driven, which we control.
2. **Ensemble across 2 providers**: run Bull twice (Groq + Cerebras substitute / OpenRouter), same for Bear. Take mean confidence. Cancels per-model bias.
3. **Blind judge**: after debate, feed transcript (without side labels) to a THIRD LLM: "Which argument is stronger, based only on the evidence cited?" Journal the judge's verdict; use it as a tiebreaker.

**Expected lift:** cuts the Bear-bias by half → Risk vetoes drop from 23% to ~10% → directional call rate rises from 14% to ~30%. On the same 44-date sample that means ~13 bets instead of 6, roughly doubling our statistical resolution per run.

---

## Upgrade 2 — Kelly-fractional position sizing tied to composite conviction

**What we observed:** All 6 directional bets were 0.03-0.12 fraction of capital. On 2024-11-24 the machine went SELL with bear conf 0.62, net_bias −2.00, and Kronos off → but sized only 0.10. On 2024-12-02 the machine went BUY with weaker signals (bull 0.65, bear 0.42, Kronos +3.2%) → but sized 0.12, its BIGGEST bet. **Sizing is uncorrelated with signal quality.**

**Fix (3-4 hours):**
- New file `argos/portfolio/sizing.py` implementing:
  - **Kelly-lite**: `size = clip( edge / variance × conviction_multiplier, 0, risk_cap )`
  - Where `edge` = expected return implied by composite bias × Kronos alignment
  - `variance` = realized_vol_20d × ATR_pct
  - `conviction_multiplier` = geometric mean of {|net_bias|/5, |kronos_move|/10, agent_agreement_score}
  - `risk_cap` = min(risk_verdict.max_position_size, sector_cap, per-name cap)
- Portfolio Manager prompt updated to pass ALL of these to the sizing function instead of guessing a number.

**Expected lift:** on the 2024-11-24 SELL (strongest bear signal), size should have been 0.25-0.30 not 0.10; on 2024-12-02 BUY (weakest signal) size should have been 0.02 or vetoed. Same 6 bets, 3× the size-weighted P&L. On a positive-edge system this compounds meaningfully; on a break-even system it just widens the variance — hence why we do Upgrade 1 first.

---

## Upgrade 3 — Stops and exits (the machine currently has none)

**What we observed:** BUY on 2024-12-02, next-day −0.23%. The "position" from that BUY has no defined exit — in the current design it's just a one-day directional bet scored against day+1. If Argos were live-trading, that position would sit open with no protection.

**Fix (2 hours):**
- Every `FinalDecision` gains four fields: `stop_price`, `target_price`, `time_stop_bars`, `trailing_stop_atr`
- Stop = entry − `2 × ATR(14)` for longs (or + for shorts) — worst-case loss capped
- Target = entry ± `3 × ATR(14)` (2:1 reward:risk minimum)
- Time stop = 5 bars — if neither target nor stop hit, close and re-evaluate
- Trailing stop kicks in once price moves 1×ATR in our favor
- The `simulate.py` replay engine honors all four

**Expected lift:** on a random walk with 50% hit rate, this alone converts a break-even strategy into positive expectancy (average win 3×ATR, average loss 2×ATR = 1.5× payoff ratio). **This is the single change that turns "avoids drawdowns" into "actually makes money".**

---

## Upgrade 4 — Kronos calibration (use direction, not magnitude)

**What we observed:** Kronos predicted −12.9% and +3.2%; realized −0.9% and −0.2%. Magnitude ratio is 14× off on the big call. Direction was right on the −12.9 call, wrong on the +3.2 call. So Kronos gets ~50% on direction and ~5% on magnitude — its magnitude is basically noise.

**Fix (1 hour):**
- In `argos/data/kronos_client.py`, add `kronos_direction_score`:
  - `sign(predicted_move_pct)` × `min(|predicted_move_pct|/2, 1.0)` — clamp to [-1, +1]
- In `technicals_node`, feed only `kronos_direction_score` + `uncertainty` (not raw %) to the LLM
- Rewrite technicals prompt to say: **"Kronos predicts direction UP/DOWN with confidence C. Treat magnitude as unreliable."**
- Journal both raw and calibrated to compare over time

**Expected lift:** eliminates the "Kronos said −12%!" over-reaction. The 2024-11-19 to 2024-11-28 HOLD streak partly happened because indicator net_bias was small (−2.5 to +2.5) but the LLM saw big Kronos numbers and hedged. Calibrated Kronos = LLM makes fewer scared HOLDs.

---

## Upgrade 5 — Multi-horizon Kronos (use ALL 10 forecast bars, not just bar+10)

**What we observed:** `predicted_move_pct` is just `(forecast[-1]/last_close - 1)` — the 10-bar cumulative. Kronos returns 10 individual forward bars but we throw away the shape.

**Fix (1 hour):**
- Compute `kronos_short_signal` = mean move over bars 1-3
- Compute `kronos_swing_signal` = mean move over bars 5-10
- Compute `kronos_shape_regime` = one of {steady-up, steady-down, V-bottom, inverted-V, chop} from the trajectory
- Emit all three into the technicals snapshot

**Expected lift:** on 2024-12-02 the +3.2% 10-bar forecast could have been "small bump then decline" (V-shape) instead of "steady rise". LLM sees the shape → doesn't buy the top of a small bump.

---

## Upgrade 6 — Regime-conditioned indicator weights

**What we observed:** every date's `indicator_composite` had the same weights applied to every indicator regardless of regime. Chop 5, uptrend 5, downtrend 5 — same weight scheme. That's why on chopping ADBL late-Nov the mean-reversion signals (RSI oversold, Stoch <20) kept firing but the machine correctly ignored them (via veto). It's a coincidence — the current weights aren't regime-aware, we just get lucky when Risk Manager over-rides them.

**Fix (2-3 hours):**
- New file `argos/indicators/weights.py`
- Three weight profiles: `TREND_WEIGHTS`, `CHOP_WEIGHTS`, `VOLATILE_WEIGHTS`
- Trend regime: EMA-crosses / MACD / ADX get 2×; RSI oversold gets 0.3× (don't fade a trend)
- Chop regime: RSI, Stoch, BB-touch get 2×; MACD / EMA cross gets 0.3×
- Volatile regime: cut all weights by 40% (uncertainty penalty)
- Selection driven by `snap.trend.regime` + `snap.volatility.vol_regime`

**Expected lift:** RSI oversold at 27 in a downtrend is a trap; the same RSI in a chop is a real edge. Applying weights conditionally should turn ~5-10% of current false signals into ignored ones and boost hit rate on the ~30% of dates where the regime is clear.

---

## Upgrade 7 — Multi-asset screener (biggest quota-efficient lift)

**What we observed:** running one-ticker daily forces the machine to trade OR hold on THAT stock only. But some days no NEPSE stock deserves a trade; other days three do. A screener flips the framing: **"Given 100 tradeable NEPSE symbols today, pick the top 3 opportunities."** Now the machine spends its LLM budget on the 3 most promising, not on daily grinding through one boring name.

**Fix (4-5 hours):**
- New file `argos/screener/rank.py`
- Step 1 (cheap, no LLM): compute `IndicatorSnapshot` for all ~340 NEPSE items via `nepse_api.as_of_today()`. For each: `net_bias × bull_ringer_score × (1 - vol_regime_penalty)`
- Step 2: keep top 5 bullish + top 5 bearish by score → 10 candidates
- Step 3: run the full 7-agent stack on ONLY those 10 → maybe 3 result in BUY/SELL
- Result: 3 quality directional calls per day instead of ~1 per week per ticker

**Expected lift:** shifts the machine from "wait for one stock to give signal" (which happened 6 times in 44 days) to "always find the best 3 signals across the market" (guaranteed 3-per-day). Over 44 days that's ~130 directional calls — enough for statistically significant edge detection.

**Quota math:** step 1 is pure Python, no LLM. Step 3 = 10 candidates × 7 agents = 70 LLM calls per day (vs current 7/day). Still comfortably within Groq's 14,400/day cap with Gemini + OpenRouter as fallbacks.

---

## Upgrade 8 — Self-supervised weight learning from the journal

**What we observed:** the decision journal has attempt_id, indicator_signals_fired, and — critically — realized next-day return can be joined by ticker+date. That's a labeled dataset **the system generates for itself every day.**

**Fix (4-6 hours):**
- New file `argos/learn/journal_regression.py`
- Every night: load last 60 days of journals, join with next-day returns
- Fit a lightweight logistic regression: `P(next_day_up) ~ f(each_indicator_signal, kronos_direction, agent_confidences, regime)`
- Coefficients become NEW weights for the `IndicatorSnapshot.fired_signals` list
- Version the weights file (`data/weights/v001.json`), Argos tags decisions with `strategy_version`

**Expected lift:** the biggest lever in the whole system. Once the journal has enough rows, this loop starts converging on which signals ACTUALLY predict next-day returns in NEPSE (probably a different set than what works in US equities). This is machine-learning-from-your-own-mistakes, done cheaply.

**Requires:** ~60 dates of journal data first (which we now have on ADBL). Extending to 10 tickers × 60 dates = 600 rows makes this actually predictive.

---

## Upgrade 9 — Vote-thresholded veto (less trigger-happy Risk Manager)

**What we observed:** Risk Manager vetoed 10/44 dates. Some vetoes were clearly right (2024-11-19 net_bias −2.5 & bear 0.78 → veto → price barely moved). Some were clearly wrong (2024-12-05 bear 0.78 & net_bias +3.25 → veto → price fell −2.23% — a real short opportunity blocked).

**Fix (1 hour):**
- Risk Manager veto currently a single LLM call. Replace with a **weighted vote**:
  - Vote 1: LLM verdict (as today)
  - Vote 2: `|bull_conf - bear_conf| < 0.3` → auto-veto (mixed signals)
  - Vote 3: `vol_regime == "high"` AND `|net_bias| < 2.0` → auto-veto (high vol + weak signal)
  - Vote 4: `sector_exposure_pct > cap` → auto-veto (concentration risk)
- Trade proceeds only if AT MOST 1 of the 4 votes vetoes.

**Expected lift:** on 2024-12-05 the LLM would still have vetoed but the 3 rule-based checks would have all cleared (strong signal, low vol, no position) → total 1/4 vetoes → trade proceeds → captures −2.23%. Rough estimate: 30-40% of current false vetoes turn into trades, half of which are winners.

---

## Upgrade 10 — Merolagani response caching (unlocks fast iteration on weights)

**What we observed:** every backtest date re-hits Merolagani for 400+ bars. Same bars, over and over. That's why a 44-date run took 33 minutes — half of that time was waiting on Merolagani.

**Fix (1 hour):**
- `data/cache/nepse_ohlcv/{symbol}.parquet` with incremental append
- Cache lookup first; only fetch bars NEWER than the cache max date
- Rate limit still applied (0.5 req/s) but per-symbol cache means one fetch per symbol per day

**Expected lift:** 44-date backtest drops from 33 min to ~8 min. Enables Upgrade 8 (nightly weight-learning) to run in seconds not minutes.

---

## Upgrade 11 — Explicit exit-signal agent (parallel to entry pipeline)

**What we observed:** current architecture is "should I open a position today?" — never asks "should I close an existing position?"

**Fix (3 hours):**
- New node `argos/nodes/exit_manager.py`
- Runs BEFORE the analyst fan-out, IF `portfolio_state.positions[ticker]` exists
- Prompt: "You hold N shares of ADBL at price P entered on date D. Current price is X. Stop is S, target is T. Given the fresh IndicatorSnapshot, exit or hold?"
- Output: `{action: hold_position | exit_at_market | tighten_stop | move_to_breakeven}`
- If exit fires, the graph short-circuits — no analyst debate, direct decision

**Expected lift:** losing trades don't compound. A stop-loss at 2×ATR + a time-stop at 5 bars is the difference between a −5% capped loss and a −20% catastrophe. Prevents the one bad trade that destroys a month of gains.

---

## Upgrade 12 — Backtest cost accounting + broker commission model

**What we observed:** my simulator uses 5bps slippage + 10bps commission. Actual NEPSE retail broker cost is ~0.4% per side, plus SEBON fee, plus 20% capital gains on profits held < 1 year.

**Fix (1 hour):**
- Update `argos/simulate.py::Simulator` with realistic Nepal numbers:
  - Broker commission: 0.4% buy + 0.4% sell = 0.8% round-trip
  - SEBON fee: 0.015%
  - Capital gains: 20% on realized short-term profits (or 7.5% long-term)
- Add per-trade breakeven line: a trade must return > 1.0% just to beat the fee drag
- Rework the `--simulate` report to show gross P&L, fees paid, net P&L, effective hit rate needed to break even

**Expected lift:** this doesn't improve accuracy but it **stops us fooling ourselves** with unrealistic 15-bps assumptions. The 44-date run's −0.08% "with friction" P&L becomes −1.5% with real Nepal costs — which means Upgrade 3 (stops + 2:1 R:R) becomes non-optional, not nice-to-have.

---

## What NOT to build next (opinionated)

- **The 10-ticker overnight run.** As you said, quota burn for a coin-flip data point. Do Upgrade 1 first so subsequent runs produce meaningful data.
- **More indicators** (Ichimoku, Fibonacci, etc.). We already have 25+ signals firing per date and Argos ignores most of them via HOLD. Adding more won't fix the paralysis.
- **A prettier dashboard.** No amount of Streamlit fixes an untradeable strategy.
- **Fine-tuning Kronos on NEPSE.** Kronos direction is already 50-60% right; the bottleneck is what we DO with that signal, not the signal quality.

---

## The path to "actually makes money"

Do in this order:

1. **Upgrade 1** (bias fix, 2h) — unlocks meaningful debate
2. **Upgrade 3** (stops + R:R, 2h) — turns break-even into positive expectancy
3. **Upgrade 10** (cache, 1h) — makes iteration 4× faster
4. **Upgrade 2** (Kelly sizing, 3h) — sizes bets by conviction
5. **Upgrade 4** (Kronos calibration, 1h) — kills a source of LLM confusion
6. **Upgrade 12** (real Nepal fees, 1h) — grounds all P&L numbers in reality
7. **Upgrade 9** (vote-thresholded veto, 1h) — machine trades more often
8. **Upgrade 7** (multi-asset screener, 5h) — biggest structural change, needs everything above
9. **Upgrade 8** (self-supervised learning, 6h) — the compounding loop

**Total: ~22 hours of focused work.** After that we re-run a 60-date backtest and expect:
- Directional call rate: 14% → 30-40%
- Directional hit rate: 50% → 55-58% (regime weights + calibrated Kronos + real debate)
- Position sizing: 0.05 avg → 0.15 avg on high-conviction bets
- Wall time per backtest: 33 min → 8 min (cache)
- P&L on ADBL over Oct-Dec 2024: −0.08% → **+3% to +8%** vs buy-hold's −10%.

At that point the "always ends in profit" ambition is still not mathematically achievable (nothing in markets is), but the machine's *expected value* is positive after realistic fees, which is what "profitable trading system" actually means.

---

## Two honest lines to end on

**Nothing in markets is "never wrong."** Anyone selling that is selling snake oil. What we CAN build: a system whose EXPECTED value per trade is positive after fees, whose drawdowns are capped by stops, and whose worst months are boring not catastrophic. That's a real edge.

**The current Argos already does capital defence extremely well** — ADBL fell 10% and the machine only lost 0.08%. The 12 upgrades above are about **converting defense into offense** — not making the machine more paranoid.
