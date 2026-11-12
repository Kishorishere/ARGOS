/* Phase 0 Snapshot API types — mirror server/services/snapshot.py and
   server/routers/{snapshot,ohlcv,decisions,analyze}.py. */

export interface PriceBlock {
  last?: number;
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
  prev_close?: number | null;
  change?: number;
  change_pct?: number;
  last_bar_date?: string;
  n_bars?: number;
  error?: string;
}

/* The indicator snapshot is a deeply nested pydantic model dump. Typed as
   loose `Record<string, unknown>` at the edge; panels pull named fields
   defensively with the helpers in lib/pick.ts. */
export type IndicatorBlock = Record<string, unknown> & { error?: string };

export interface FundamentalsBlock {
  latest_quarter?: string;
  pe?: number;
  pb?: number;
  roe_pct?: number;
  roa_pct?: number;
  eps?: number;
  book_value?: number;
  revenue?: number;
  net_profit?: number;
  n_quarters_available?: number;
  source_path?: string;
  error?: string;
}

export interface Position {
  ticker: string;
  sector?: string;
  quantity?: number;
  avg_price?: number;
  market_price?: number;
  market_value?: number | null;
}

export interface PortfolioBlock {
  position?: Position | null;
  held?: boolean;
  portfolio_context?: Record<string, unknown> | { error: string };
  error?: string;
}

export interface Freshness {
  as_of?: string;
  collected_at?: string;
  ohlcv_age_days?: number;
  ohlcv_last_bar?: string;
  fundamentals_latest_quarter?: string;
}

export interface Snapshot {
  ticker: string;
  as_of: string;
  live: boolean;
  price: PriceBlock;
  indicators: IndicatorBlock;
  fundamentals: FundamentalsBlock;
  portfolio: PortfolioBlock;
  freshness: Freshness;
  errors: string[];
  duration_s: number;
}

export interface OhlcvBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OhlcvResponse {
  ticker: string;
  as_of: string;
  live: boolean;
  lookback_days: number;
  n_bars: number;
  date_range?: string;
  ohlcv: OhlcvBar[];
}

export type Stage = "backtest" | "walk_forward" | "paper" | "live";

export interface DecisionRow {
  ticker: string;
  as_of: string;
  source: "backtest" | "walk_forward" | "paper" | "live" | "legacy";
  stage: Stage;
  n_triggers?: number;
  action?: "buy" | "sell" | "hold" | null;
  confidence?: number | null;
  position_size?: number | null;
  gate_proceed?: boolean | null;
  gate_skipped?: boolean;
  attempt_id?: string;
  argos_version?: string;
  journal_path: string;
}

export interface DecisionsResponse {
  ticker: string;
  from: string | null;
  to: string | null;
  sources: string[];
  n: number;
  decisions: DecisionRow[];
}

export interface AnalyzeResponse {
  run_id: string;
  ticker: string;
  as_of: string;
  live: boolean;
  stream_url: string;
}

/* ── Phase B triggers ────────────────────────────────────────────────── */

export type TriggerDirection = "bullish" | "bearish" | "neutral";
export type TriggerSource = "technical" | "scheduled" | "fundamental" | "news";

export interface Trigger {
  ticker: string;
  as_of: string;
  kind: string;
  direction: TriggerDirection;
  magnitude: number;
  detail: string;
  source: TriggerSource;
}

export interface TriggersForTicker {
  ticker: string;
  as_of: string;
  n: number;
  triggers: Trigger[];
  error?: string;
}

export interface TriggersIndex {
  as_of: string;
  universe_path: string | null;
  n_tickers: number;
  tickers: TriggersForTicker[];
}

/* ── Phase E trust features ───────────────────────────────────────────── */

export interface OverrideRow {
  id: string;
  logged_at: string;
  ticker: string;
  as_of: string;
  committee_action: string | null;
  committee_confidence: number | null;
  user_action: string;
  user_reason: string;
  run_id: string | null;
  attempt_id: string | null;
  journal_path: string | null;
}

export interface OverrideBody {
  ticker: string;
  as_of: string;
  committee_action?: string | null;
  committee_confidence?: number | null;
  user_action: string;
  user_reason: string;
  run_id?: string | null;
  attempt_id?: string | null;
  journal_path?: string | null;
}

export interface CalibrationBin {
  confidence_bin_lo: number;
  confidence_bin_hi: number;
  n: number;
  empirical_accuracy: number | null;
}

export interface CalibrationResponse {
  horizon: number;
  n_records: number;
  n_scored: number;
  overall_accuracy: number | null;
  bins: CalibrationBin[];
}

export interface CostTodayResponse {
  date: string;
  n_records: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  calls: number;
  usd_estimate: number;
  usd_per_1k_tokens: number;
}

export interface AbstentionRow {
  ticker: string;
  n: number;
  n_hold: number;
  n_buy: number;
  n_sell: number;
  hold_rate: number;
  mean_conf_on_hold: number | null;
  mean_conf_on_trade: number | null;
}

export interface AbstentionResponse {
  source: string;
  n_tickers: number;
  tickers: AbstentionRow[];
}

/* ── Phase C ownership + news ─────────────────────────────────────────── */

export interface Form4Event {
  ticker: string;
  filed_at: string;
  period_of_report: string | null;
  reporting_owner: string | null;
  owner_title: string | null;
  is_director: boolean;
  is_officer: boolean;
  is_ten_percent_owner: boolean;
  transaction_date: string | null;
  transaction_shares: number | null;
  transaction_price: number | null;
  direction: "A" | "D" | null;
  security_title: string | null;
  accession: string;
  url: string;
}

export interface InstitutionalHolder {
  ticker: string;
  filer_name: string;
  cik: number | null;
  shares: number | null;
  value_usd: number | null;
  pct_outstanding?: number | null;
  as_of: string | null;
  source_url: string | null;
}

export interface OwnershipResponse {
  ticker: string;
  cik?: number | null;
  form4: Form4Event[];
  holders: InstitutionalHolder[];
  holders_note?: string;
  form4_error?: string;
  holders_error?: string;
  error?: string;
}

export interface NewsItem {
  ticker: string;
  headline: string;
  summary: string;
  source: string;
  category: string;
  url: string;
  datetime: string;
  image: string | null;
  related: string | null;
}

export interface NewsResponse {
  ticker: string;
  from: string;
  to: string;
  items: NewsItem[];
  cached?: boolean;
  error?: string;
}
