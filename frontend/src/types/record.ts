/* Decision-journal record types — mirrors argos/journal.py::build_record.
 * Grounded in real records: 20 stored keys today, `judge_verdict` added
 * going forward. Everything that can be null in production is optional/nullable.
 */

export type Signal = "bullish" | "bearish" | "neutral";
export type Action = "buy" | "sell" | "hold";

export interface KeyFactorSummary {
  signal: Signal;
  confidence: number;
  key_factors: string[];
  reasoning: string;
  indicator_snapshot: IndicatorSnapshot | null;
}

export interface FiredSignal {
  indicator: string;
  value: number | string;
  threshold: string;
  direction: string;
  weight: number;
  contribution?: number;
  note: string;
}

export interface IndicatorGroup {
  signals?: FiredSignal[];
  [k: string]: unknown;
}

export interface IndicatorSnapshot {
  ticker: string;
  as_of_date: string;
  last_close: number;
  bars_used: number;
  momentum?: IndicatorGroup;
  trend?: IndicatorGroup;
  volatility?: IndicatorGroup;
  volume?: IndicatorGroup;
  structure?: IndicatorGroup;
  candles?: IndicatorGroup;
  divergences?: IndicatorGroup;
  composite_bull_score: number;
  composite_bear_score: number;
  net_bias_score: number;
  fired_signals: FiredSignal[];
  multi_timeframe?: Record<string, IndicatorGroup>;
  confluence?: Record<string, unknown>;
}

export interface AnalystSignals {
  fundamentals: KeyFactorSummary | null;
  sentiment: KeyFactorSummary | null;
  technicals: KeyFactorSummary | null;
}

export interface HorizonForecast {
  horizon: string;
  direction: "up" | "down" | "flat";
  probability_up: number;
  expected_return_pct: number;
  p10_return_pct: number;
  p50_return_pct: number;
  p90_return_pct: number;
  confidence: number;
  key_drivers: string[];
}

export interface EnsembleContribution {
  source: string;
  strength: number;
  coefficient: number;
  log_odds: number;
  note: string;
}

export interface EnsembleHorizon {
  horizon: string;
  probability_up: number;
  direction: "up" | "down" | "flat";
  log_odds_total: number;
  contributions: EnsembleContribution[];
  top_drivers: string[];
}

export interface MultiHorizonForecast {
  symbol: string;
  as_of_date: string;
  last_close: number;
  horizons: Partial<Record<string, HorizonForecast>>;
  ensemble_by_horizon: Partial<Record<string, EnsembleHorizon>>;
}

export interface DebateSide {
  position: "buy" | "sell";
  strongest_evidence: string;
  rebuttal_of_opposing_view: string;
  confidence: number;
  reasoning: string;
  evidence_summary: string;
}

export interface Debate {
  bull: DebateSide | null;
  bear: DebateSide | null;
  rounds_completed: number;
}

export interface TranscriptEntry {
  round: number;
  side: "bull" | "bear";
  position: "buy" | "sell";
  confidence: number;
  strongest_evidence: string;
  rebuttal_of_opposing_view: string;
  reasoning: string;
  evidence_summary: string;
}

export interface JudgeVerdict {
  winner: "A" | "B" | "tie";
  winner_side: "bull" | "bear" | "tie";
  confidence: number;
  reasoning: string;
  key_factor: string;
}

export interface VetoVote {
  name: string;
  veto: boolean;
  reason: string;
}

export interface RiskVerdict {
  veto: boolean;
  max_position_size: number;
  reasoning: string;
  risk_factors: string[];
  veto_votes: VetoVote[];
  veto_count: number;
}

export interface SizingTrace {
  size: number;
  edge_pct?: number;
  variance_pct2?: number;
  kelly_signed?: number;
  conviction_mult?: number;
  agent_agreement?: number;
  atr_pct_used?: number;
  risk_cap?: number;
  hard_cap?: number;
  fraction_of_kelly?: number;
  dd_scale?: number;
  dd_status?: string;
  dd_reason?: string;
  reason?: string;
}

export interface FinalDecision {
  action: Action;
  confidence: number;
  position_size: number;
  rationale: string;
  dissent_notes: string;
  stop_loss_pct: number | null;
  take_profit_pct: number | null;
  trailing_stop_atr: number | null;
  time_stop_bars: number | null;
  sizing_trace: SizingTrace | null;
  sizing_llm_original: number | null;
}

export interface PortfolioState {
  cash: number;
  total_equity: number;
  cash_pct: number;
  gross_exposure_pct: number;
  day_pnl: number;
  day_pnl_pct: number;
  n_positions: number;
  positions: unknown[];
  sector_weights_pct: Record<string, number>;
}

export interface IndicatorComposite {
  composite_bull_score: number;
  composite_bear_score: number;
  net_bias_score: number;
  trend_regime: string;
  vol_regime: string;
  bull_ringer_score: number;
  bear_ringer_score: number;
}

export interface OHLCVSummary {
  rows: number;
  date_range: string;
  last_5_closes: number[];
  last_close: number | null;
  last_date: string | null;
}

export interface JournalRecord {
  attempt_id: string | null;
  attempted_at: string;
  argos_version?: string;
  duration_s: number;
  ticker: string;
  date: string;
  run_id?: string;
  ohlcv_summary: OHLCVSummary | null;
  portfolio_state: PortfolioState | null;
  analyst_signals: AnalystSignals | null;
  kronos_forecast: unknown;
  multi_horizon_forecast: MultiHorizonForecast | null;
  indicator_composite: IndicatorComposite | null;
  indicator_signals_fired: FiredSignal[];
  debate: Debate | null;
  debate_transcript: TranscriptEntry[];
  judge_verdict?: JudgeVerdict | null;
  risk_verdict: RiskVerdict | null;
  final_decision: FinalDecision | null;
  analyst_prompts?: Record<string, { system: string; user: string; anonymization?: string }>;
  errors: string[];
  error?: string;
}
