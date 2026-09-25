# Argos → Professional-Grade NEPSE Direction Engine

> **Goal:** the highest DIRECTIONAL accuracy achievable on NEPSE, with
> 1-day / 1-week / 1-month horizon forecasts and projected profit ranges,
> under the reality that Nepal is T+2 (no intraday), retail-dominated, and
> corporate-action-heavy.
>
> **Honest ceiling on hit rate:** the best equity long/short funds in the
> world hit 52-56% directional at daily horizon (Two Sigma, Renaissance,
> AQR). What lets them print money isn't 80% accuracy — it's edge
> compounded across thousands of small bets and disciplined risk. **A
> realistic Argos target for NEPSE daily direction is 58-64%**, and
> **68-75% for the 1-week horizon**, which is where our real edge lives
> because Nepal's slow-moving retail flow makes multi-day trends more
> predictable than any 1-day move.
>
> **80% is achievable only when we FILTER heavily** — throw away 90% of
> setups and only trade the top 10% (highest-conviction confluence). Then
> we can honestly hit 75-82% on very few, very good bets.
>
> This document is the complete plan to get there.

---

## Part 0 — Reality check: what NEPSE actually is

Before piling on features, honest observations about NEPSE that make it
different from S&P/NIFTY quant work:

1. **T+2 settlement + no intraday sells** — hold at least 3 days.
   Direction on 3-10 day horizon is what matters, not tick-level.
2. **±10% circuit breakers** per day, both directions. Kills any strategy
   that needs stops tighter than ~2%.
3. **Heavy retail flow with clear behavioral patterns** — dividend
   ex-dates, quarterly earnings, IPO announcements move stocks
   predictably.
4. **~330 traded scripts, ~150 with meaningful liquidity** (turnover
   > 5 lakhs NPR/day). The universe is small enough to score every day.
5. **Sector-driven** — 9 defined sectors move in packs. Commercial Banks
   often lead; hydropower lags rainfall data by weeks; life insurance
   moves on regulator news.
6. **News is in Nepali + English** — most Western LLMs miss ~40% of
   sentiment because sources like Karobar, Bikaskonews, ekantipur.com
   publish in Nepali.
7. **Macro-sensitive** — remittance inflows (~25% of GDP), NRB interest
   rate cycles, RSA balance-of-payments, monsoon quality (for hydro),
   Indian NIFTY correlation (~0.35 monthly).
8. **Insider-heavy** — floorsheet data (broker-level trades) leaks
   information. Watching which brokers accumulate is a real edge.

Everything below is designed around these realities, not around
S&P-shaped assumptions.

---

## Part 1 — What professional quants actually do (and how we adapt each)

### 1.1 Ensemble of independent models with meta-learner

**What pros do:** they don't rely on one signal. They combine 5-15
independent models (technical, statistical, ML, sentiment, macro,
alt-data) via a meta-learner (usually XGBoost or logistic regression)
that OUTPUTS the final signal + confidence.

**Argos today:** 7 LLM agents debate then a Portfolio Manager
synthesizes. That's ONE aggregation path.

**Upgrade P1 — Ensemble Meta-Layer (M, 6h)**
- New file `argos/ensemble/meta.py`
- Inputs: `IndicatorSnapshot` (technical), Kronos direction score, LLM
  bull/bear/judge confidences, macro overlay, sentiment score, floorsheet
  score
- Output: `EnsembleSignal(direction, confidence, contributions_by_source)`
- Method: weighted logistic regression trained on past journal
  outcomes. Weights refit weekly from realized hit rates per source.
- **Expected lift:** +3-5% hit rate by weighing sources by their actual
  predictive power (a source that's 45% right gets less weight than one
  that's 62% right).

### 1.2 Bayesian belief updating

**What pros do:** start with a prior (e.g., 50/50 on direction), update
sequentially as evidence arrives — each new signal shifts the posterior
by its historical likelihood ratio. Reaches high-conviction states
gracefully.

**Argos today:** each agent gives a snapshot opinion; the Portfolio
Manager compresses everything at the end.

**Upgrade P2 — Sequential Bayesian Aggregator (M, 4h)**
- New file `argos/ensemble/bayesian.py`
- Each signal source has an empirical `P(direction=up | signal fired)`
  from journal history (Bayes factor).
- Combine multiplicatively (or via log-odds sum for numerical stability).
- Emits `posterior_p_up` in [0..1] with clear semantics.
- **Expected lift:** cleaner confidence numbers; lets us use the
  `posterior >= threshold` filter for U0 (80% target).

### 1.3 Multi-timeframe confluence

**What pros do:** signal must fire on multiple timeframes before
they act. "Weekly trend up, daily RSI oversold, hourly bullish
divergence" is a much higher-quality setup than any one of those alone.

**Argos today:** single-timeframe (daily) indicators only.

**Upgrade P3 — Multi-Timeframe Snapshot (M, 5h)**
- Extend `compute_snapshot` to accept a `timeframe` parameter
- Add `resample_ohlcv` helper: daily → weekly (Fri close), daily → 4h
  synthesis via VWAP where available
- `MultiTimeframeSnapshot` container with daily / weekly / monthly views
- Confluence score: `+1` per timeframe that agrees on direction
- **Expected lift:** requiring 3-of-3 timeframe agreement typically
  raises hit rate 10-15% on the filtered subset — this is the biggest
  single lever toward 80%.

### 1.4 Sector rotation & relative strength

**What pros do:** rank sectors by 1w/1m/3m returns vs the index. Buy
strongest sector's leaders; short weakest sector's laggards. Sector
rotation is a documented alpha source.

**Argos today:** ticker-by-ticker, no sector context.

**Upgrade P4 — NEPSE Sector Rotation Engine (M, 5h)**
- New `argos/data/nepse_sectors.py` — pull 9 sector index closes daily
- New `argos/screener/sector_rank.py` — compute 1w/1m/3m relative
  strength vs NEPSE index for each sector
- Screener now weights: `symbol_score × sector_score`
- Portfolio Manager sees "you're buying a Commercial Bank in the top-2
  strongest sector this month" as extra confidence
- **Expected lift:** +4-8% hit rate on positions aligned with sector
  rotation direction.

### 1.5 Market breadth filters

**What pros do:** don't trade against breadth. If advances-declines is
strongly negative on a broad-market signal to buy, skip it.

**Argos today:** we HAVE breadth via `nepse_api.market_breadth()` but
don't use it as a decision input.

**Upgrade P5 — Breadth-Filtered Signals (S, 2h)**
- Feed `market_breadth()` into the Risk Manager and Portfolio Manager
  as a hard filter: "if today's A/D ratio < 0.3 AND signal is BUY,
  require agreement on all three timeframes."
- Add breadth to the ensemble as a signal source.
- **Expected lift:** cuts about 15% of losing trades on down-market days.

### 1.6 Volatility regime forecasting

**What pros do:** predict tomorrow's realized vol with GARCH(1,1) or
HAR-RV models. Position size scales inversely with predicted vol.

**Argos today:** we use CURRENT ATR% only, no forecast.

**Upgrade P6 — GARCH Vol Forecast (M, 4h)**
- Use `arch` package (pure-python) for GARCH(1,1)
- 1-day / 1-week vol forecasts feed into Kelly sizing's variance
  denominator
- Also emit `vol_regime_forecast` ∈ {expanding, contracting, stable}
- **Expected lift:** prevents "we sized big and then vol spiked"
  drawdowns — improves risk-adjusted returns even if hit rate is flat.

### 1.7 Corporate actions calendar (NEPSE-critical)

**What pros do:** every institution tracks the earnings/dividend/rights/
bonus calendar. Prices move systematically 2 weeks before and 3 days
after these events. Trading around them without the calendar is
guaranteed to eat drawdowns.

**Argos today:** blind to corporate actions.

**Upgrade P7 — NEPSE Corporate Actions Calendar (L, 8h)**
- Scrape ShareSansar's "Book Close" + "Dividend" + "Rights" + "AGM"
  calendars daily to `data/calendar/corporate_actions.jsonl`
- Store as `{symbol, action_type, book_close_date, ex_date, cash_dividend_pct, bonus_pct, rights_ratio}`
- Feed into Risk Manager: "book close in 3 days on NABIL, expect 2-4%
  drop on ex-date; DO NOT enter longs unless holding through"
- Post-ex-date bounce is a real edge (~55% hit rate for 5-day post-ex
  reversion on Commercial Banks per Nepal literature)
- **Expected lift:** avoids the 3-5 ex-date knife-catch losers/quarter.
  Also unlocks a dedicated `dividend_ex_reversion` strategy.

### 1.8 Fundamental screening (quarterly earnings)

**What pros do:** every stock gets a fundamental score — earnings
growth, P/E vs sector median, book value, ROE trajectory. Combined
with technicals, this is the quant approach.

**Argos today:** the fundamentals agent uses Gemini's grounded search,
which is unreliable for NEPSE (misses Nepali filings).

**Upgrade P8 — Structured Fundamentals from ShareSansar (L, 8h)**
- Scrape quarterly report table from
  `www.sharesansar.com/companies/{SYMBOL}/quarterly-financial-report`
- Store `{symbol, quarter, revenue, net_profit, eps, roe, npl_ratio,
  distributable_profit, ...}` in `data/fundamentals/{symbol}.jsonl`
- New `argos/fundamentals/scoring.py` — sector-relative percentile
  ranking on 8-10 fundamental metrics; output composite F-score 0-100
- Fundamentals agent uses this data DIRECTLY (not Gemini search)
- **Expected lift:** far more accurate fundamentals signal (F-score is
  a documented predictor of 6-month returns on emerging markets).

### 1.9 Floorsheet / smart-money analysis

**What pros do:** track broker-level trade flow to detect institutional
accumulation before it shows up in price. "Broker #7 has been the
biggest buyer of ADBL for 15 straight sessions" is a real signal.

**Argos today:** no floorsheet integration.

**Upgrade P9 — Floorsheet Ingestion + Smart-Money Score (L, 12h)**
- Scrape `nepalstock.com.np/floor-sheet` (or the terminal's already-
  existing scraper) to `data/floorsheet/{date}.parquet`
- Compute per-symbol daily:
  - Top-3 buying broker concentration (% of volume)
  - Top-3 selling broker concentration
  - Broker rotation (new brokers entering the top-10?)
  - Persistent-buyer flag (same broker >5 sessions in top-3 buyers)
- Feed into ensemble as `smart_money_score` in [-1, +1]
- **Expected lift:** floorsheet-informed entries hit 60-70%+ historically
  on Nepal papers — this is one of the biggest edges on NEPSE
  specifically because the data is public and few systems use it.

### 1.10 Nepali-language sentiment

**What pros do:** dedicated NLP on native-language sources gives
sentiment signals days before English-only feeds pick them up.

**Argos today:** Gemini-grounded English search only.

**Upgrade P10 — Nepali Sentiment Pipeline (M, 6h)**
- Scrape ekantipur, Bikaskonews, karobardaily, Merolagani news for
  each ticker
- Translate via Gemini (which handles Nepali well) — batch requests
- Score sentiment 0-100 per article; aggregate per ticker per day
- New `sentiment_score_np` fed to ensemble
- **Expected lift:** ~+3-5% hit rate on stocks with high news volume;
  bigger on event-driven names.

### 1.11 Macro overlay

**What pros do:** every position sits under a macro regime filter —
interest rate direction, USD strength, oil, gold, cross-market
correlations. Bull setups fail when macro turns hostile.

**Argos today:** no macro context.

**Upgrade P11 — Nepal Macro Regime (M, 6h)**
- Track daily: NRB inter-bank rate, USD/NPR, gold NPR/tola, oil NPR/L,
  NIFTY close, remittance monthly print (when available)
- Rolling regime detector: `{risk_on, neutral, risk_off}` from a simple
  logistic regression trained on next-month NEPSE index direction
- Fed to Risk Manager as a **hard filter**: `risk_off` regime + BUY
  requires 3-of-3 timeframe confluence (per P3)
- **Expected lift:** avoids April/October drawdown seasons where NEPSE
  historically loses 5-10%.

### 1.12 Adaptive per-signal accuracy tracking

**What pros do:** every signal has a rolling `hit_rate_60d`. Signals
whose accuracy drops below 50% get sidelined; those above 60% get
extra weight. This is auto-selection of what's working NOW.

**Argos today:** all signals get static weights.

**Upgrade P12 — Signal Accuracy Ledger (M, 5h)**
- After each journal entry, join with realized next-day (and 5-day)
  return; label each fired signal as hit or miss
- Store per-signal rolling 60-decision hit rate in
  `data/signal_ledger.jsonl`
- Ensemble meta-layer (P1) reads this ledger to weight signals
- Retired signals (hit rate < 45% over 60+ obs) drop to 0 weight until
  they recover
- **Expected lift:** self-cleaning system. +2-4% hit rate compounding
  over time.

### 1.13 Hurst exponent — mean-reversion vs momentum detector

**What pros do:** compute rolling Hurst exponent per stock (60-day).
`H > 0.55` = momentum regime → use trend-following signals. `H < 0.45`
= mean-reversion regime → use fade-the-move signals. `~0.5` = random
walk → don't trade.

**Argos today:** we use `trend.regime` from ADX which is coarse.

**Upgrade P13 — Hurst Regime Detector (S, 2h)**
- New `argos/indicators/hurst.py` — rolling 60d R/S estimator
- Adds `snap.trend.hurst_exponent` and `hurst_regime` ∈ {momentum,
  mean_revert, random}
- Regime weights (from U6) refine to use Hurst not just ADX
- **Expected lift:** catches regime shifts 5-10 bars before ADX does.

### 1.14 Signal decay tracking

**What pros do:** a signal fired 8 bars ago is stale; fired today is
fresh. Weight recent firings more.

**Argos today:** all signals firing on the last bar treated equally.

**Upgrade P14 — Signal Freshness (S, 3h)**
- Persist last-fired frame per (symbol, indicator) in
  `data/cache/signal_state.json`
- `age_bars` field on each ThresholdSignal
- Weight decays `exp(-age/tau)` with tau=5 bars
- **Expected lift:** filters out "the RSI got oversold 7 days ago and
  it's still lingering there" false setups.

### 1.15 Purged K-fold cross-validation for backtest hygiene

**What pros do:** López de Prado's purged K-fold prevents label leakage
in backtests. Standard k-fold on time-series data is broken because
adjacent samples share information.

**Argos today:** simple walk-forward; no cross-validation.

**Upgrade P15 — Purged K-fold Validator (M, 4h)**
- New `argos/validation/purged_kfold.py`
- Used to validate signal accuracy claims from P12
- Prevents "this signal looks 65% right but it's leaking"
- **Expected lift:** no accuracy uplift, but PROTECTS against
  overfitting-driven false confidence.

### 1.16 Deflated Sharpe ratio

**What pros do:** when you try 30 strategy variants, the "best" one
looks amazing by chance. Deflated Sharpe corrects for the number of
trials.

**Argos today:** no such correction.

**Upgrade P16 — Deflated Sharpe (S, 2h)**
- New `argos/validation/deflated_sharpe.py`
- All strategy reports quote both raw Sharpe and deflated Sharpe
- **Expected lift:** honest performance numbers.

### 1.17 Multi-horizon forecasting (THE user's core ask)

**What pros do:** every recommendation ships with 1d / 1w / 1m
direction + expected return + confidence interval. Nobody trades on a
single "up or down tomorrow" number in isolation.

**Argos today:** implicit 1-day only.

**Upgrade P17 — Multi-Horizon Forecast Output (M, 6h)**

The centerpiece deliverable. Add `MultiHorizonForecast` schema:

```python
class MultiHorizonForecast(BaseModel):
    symbol: str
    as_of_date: str
    horizons: dict[str, HorizonForecast]  # keys: "1d", "1w", "1m"

class HorizonForecast(BaseModel):
    direction: Literal["up", "down", "flat"]
    probability_up: float  # 0..1
    expected_return_pct: float
    p10_return_pct: float  # 10th percentile of forecast dist
    p50_return_pct: float
    p90_return_pct: float
    confidence: float  # composite confidence 0..1
    key_drivers: list[str]  # top-3 signals driving this horizon
```

- 1-day: from Kronos short_horizon_pct + indicator net_bias + LLM debate
- 1-week: from Kronos swing_horizon_pct + weekly-timeframe confluence
  (P3) + sector rotation trend (P4) + Hurst regime (P13)
- 1-month: from monthly-timeframe indicators + fundamental F-score (P8)
  + corporate actions calendar (P7) + macro regime (P11)
- Each horizon has INDEPENDENT confidence — a stock can be strongly
  bearish on 1d (short-term overextended) but bullish on 1m (fundamentals
  improving).
- **Expected reporting:** for every scored symbol every day, journal
  the full multi-horizon forecast. Enables the per-day/per-week/per-month
  filters the user asked for.

### 1.18 Portfolio construction (Kelly + vol targeting)

**What pros do:** individual position size = min(Kelly, vol-target
weight). Portfolio-level: target 15% annualized vol; each position
scaled to contribute equally to portfolio variance.

**Argos today:** Kelly on individual positions, no portfolio-level
volatility target.

**Upgrade P18 — Vol-Targeted Portfolio Weights (M, 5h)**
- `argos/portfolio/vol_target.py`
- Target portfolio vol = 15% annualized (configurable)
- New position weight = min(Kelly_size, vol_target_weight, sector_cap)
- **Expected lift:** smoother equity curve; reduces max drawdown 25-40%.

### 1.19 Correlation-aware sizing

**What pros do:** two positions with 0.8 correlation are one position
in disguise. Cut both by half when adding a correlated name.

**Argos today:** ignores correlation.

**Upgrade P19 — Rolling Correlation Matrix (M, 4h)**
- Nightly job computes 60-day return correlation across held positions
- Portfolio Manager sees "your new NIC BUY has 0.75 correlation to
  your existing HIDCL long — cap combined weight at 6%"
- **Expected lift:** prevents effective over-concentration; smooths
  drawdowns.

### 1.20 Drawdown-based scale-down

**What pros do:** when equity drops X% from peak, cut all new position
sizes by 50% until equity recovers Y%. Stops death-spirals.

**Argos today:** none.

**Upgrade P20 — Drawdown Circuit-Breaker (S, 2h)**
- Track equity from journal
- If drawdown > 8%: halve all new position sizes
- If drawdown > 15%: pause new entries (only exits/adjustments)
- Reset when equity recovers to peak × 0.98
- **Expected lift:** never a catastrophic drawdown; caps monthly loss
  at ~10% even on worst-case runs.

---

## Part 2 — Multi-horizon reporting (the user's core ask)

Once P17 lands, we get per-symbol per-day forecasts on 3 horizons.
Now we need the FILTERS the user asked for.

### 2.1 Per-day view (`argos/reports/daily.py`)

For any date, journal aggregate:
- All symbols scored that day with all 3 horizon forecasts
- Which symbols crossed the "high-conviction" threshold (posterior > 0.7)
- Actual next-day realized returns joined
- Rolling hit rate per horizon over the last 30 days

Format: single JSONL row per (symbol, date) with the full forecast.

### 2.2 Per-week view (`argos/reports/weekly.py`)

Every Friday, aggregate the week:
- Distinct signals fired
- Highest-conviction weekly picks (top 5)
- Realized return vs projected return per pick
- Sector rotation shifts this week
- Which agents' opinions were most predictive

### 2.3 Per-month view (`argos/reports/monthly.py`)

End of each month:
- All 1-month forecasts issued 30 days ago vs realized
- Hit rate by conviction band (0.5-0.6 vs 0.6-0.7 vs 0.7+)
- Sector performance breakdown
- P&L attribution: which signal source contributed most
- Drawdown analysis
- Suggested weight adjustments for next month

### 2.4 The "always-on dashboard" spec

A single `argos/dashboards/nepse_watch.py` producing:
```
NEPSE INDEX: 2618.03 (-1.36% today, -3.2% week, +1.1% month)
BREADTH:     51 up / 290 down (A/D ratio 0.18 — bearish)

TOP CONVICTION PICKS (posterior > 0.70)
─────────────────────────────────────────
  BUY  NABIL   1d +1.2% (0.68) | 1w +3.5% (0.74) | 1m +8.2% (0.71)
       Drivers: bull_ringer(morning_star), sector_rank=1, macro=risk_on
  SELL UPPER   1d -0.9% (0.72) | 1w -2.8% (0.71) | 1m -6.0% (0.65)
       Drivers: divergence(RSI bear), sector_rank=8, weekly_death_cross

FRESH LOWER-CONVICTION (posterior 0.55-0.70)
─────────────────────────────────────────
  BUY  ADBL   1d +0.5% (0.62) | 1w +1.9% (0.59) | 1m +4.0% (0.55)
  ...

ROLLING HIT RATE (last 90 days)
  1-day:  62.1%   (n=284, ci [58.5%, 65.7%])
  1-week: 71.4%   (n=248, ci [67.9%, 74.8%])
  1-month: 66.2%  (n=180, ci [61.5%, 70.7%])

DRAWDOWN: -2.3% from peak    |    EQUITY: +11.4% YTD
```

**This is the artifact the user actually wants** — the "brain" as a
single readable page, refreshed daily.

---

## Part 3 — The path to 80% (honest engineering, not marketing)

**80% is a filter, not a base rate.** Here's what it actually takes:

### 3.1 Layer 1 — Base rate (with all P1-P20 upgrades)
- 1-day hit rate: **58-64%** (realistic ceiling on unfiltered NEPSE)
- 1-week hit rate: **68-75%**
- 1-month hit rate: **70-78%**

### 3.2 Layer 2 — Filter to high-conviction only
Only surface trades meeting ALL of:
- Ensemble posterior ≥ 0.70
- 3-of-3 timeframe agreement (P3)
- Sector rank in top-2 (bull) or bottom-2 (bear) (P4)
- Signal accuracy ledger of driving signals ≥ 55% (P12)
- No corporate action within 3 days (P7)
- Macro regime not hostile (P11)
- Hurst regime aligned with signal type (P13)

**On the filtered subset (probably 10-15% of raw signals), hit rate
typically jumps to 75-82%.** This is where the user's 80% target lives.

### 3.3 Layer 3 — Frequency
Filtered high-conviction: **~2-4 trades per week across the whole
NEPSE universe**. That's the trade-off — fewer trades, much higher
conviction each.

For higher frequency (~5-10 per day), accept the 58-64% base rate.

### 3.4 Reporting the trade-off

Every report should show BOTH:
- "Unfiltered universe rate: 61% (n=850, last 30d)"
- "High-conviction filter rate: 78% (n=42, last 30d)"

So the user can decide which regime to trade.

---

## Part 4 — Implementation order (build me this list, no more scope)

**Phase A — foundations to unlock everything else (~30 hours):**
1. P17 Multi-horizon forecast schema + wire (6h)
2. P3 Multi-timeframe snapshot (5h)
3. P8 ShareSansar fundamentals scraper (8h)
4. P7 Corporate actions calendar (8h)
5. P12 Signal accuracy ledger (3h)

**Phase B — sector + macro layer (~15 hours):**
6. P4 Sector rotation engine (5h)
7. P11 Nepal macro overlay (6h)
8. P5 Breadth filter (2h)
9. P13 Hurst regime (2h)

**Phase C — meta-learner + hi-conviction filter (~10 hours):**
10. P1 Ensemble meta-layer (6h)
11. P2 Bayesian aggregator (4h)

**Phase D — Nepal-specific alpha (~20 hours):**
12. P9 Floorsheet + smart-money (12h)
13. P10 Nepali sentiment (6h)
14. P14 Signal freshness (3h)

**Phase E — risk & portfolio ops (~15 hours):**
15. P6 GARCH vol forecast (4h)
16. P18 Vol-targeted portfolio (5h)
17. P19 Correlation-aware sizing (4h)
18. P20 Drawdown circuit-breaker (2h)

**Phase F — validation rigor (~8 hours):**
19. P15 Purged K-fold (4h)
20. P16 Deflated Sharpe (2h)
21. Full validation report auto-generator (2h)

**Phase G — reports (~8 hours):**
22. Daily / weekly / monthly reports (P2.1-2.3) (6h)
23. NEPSE watch dashboard (2h)

**Total: ~106 hours of focused work.**

Realistically that's 3-4 weeks of full-time work, or 2-3 months at
"a few hours per day" pace.

---

## Part 5 — Honest expectations, in one paragraph

At full build-out we can realistically expect: **60-65% hit rate on all
directional calls, 75-82% on the top-decile filtered picks, 1-week
horizon accuracy 5-8 points higher than 1-day, and 1-month higher
still**. Projected profit ranges will be quotable (p10/p50/p90) but
subject to ±30% margin of error until we have 200+ decisions per
horizon in the journal. Under real Nepal fees (0.83% breakeven per
round-trip) the strategy needs 55%+ hit rate on the FILTERED subset
just to be profitable; at 75-80% filtered hit rate we'd project net
~1.5-2.5% per month gross, ~1-1.8% per month net of fees, or
~15-25% annualized — which is real institutional-grade performance for
NEPSE. **Anyone promising 80% on ALL signals is either not measuring
honestly or overfitting on backtest data that won't hold live.** What
we CAN deliver is a system where you look at the daily dashboard,
trade only the top 2-3 filtered picks, and get to 75-80% on those
picks after 3-6 months of ledger calibration.

That's the plan.
