/* API response types — mirror server/routers/*.py */

import type { JournalRecord } from "./record";

export interface SystemStatus {
  service: string;
  market: "nasdaq" | "nepse";
  market_label: string;
  max_debate_rounds: number;
  llm_cache: {
    enabled: boolean;
    path?: string;
    total_rows?: number;
    per_agent?: Record<string, number>;
    size_bytes?: number;
    error?: string;
  };
  kronos: {
    configured: boolean;
    reachable: boolean;
    endpoint?: string;
    detail?: string;
    model?: string;
    device?: string;
  };
  journals: Record<string, MarketCounts | { error: string }>;
}

export interface MarketCounts {
  tickers: number;
  records: number;
  errors: number;
  dates: number;
}

export interface JournalTicker {
  ticker: string;
  dates: string[];
  records: number;
  errors: number;
}

export interface JournalTree {
  market: string;
  root: string;
  tickers: JournalTicker[];
}

export interface JournalDetail {
  market: string;
  ticker: string;
  date: string;
  path: string | null;
  n_attempts: number;
  attempts: JournalRecord[];
}

export interface TickerReport {
  ticker: string;
  n_records: number;
  n_errors: number;
  sharpe: number | null;
  sortino: number | null;
  max_dd: number | null;
  calmar: number | null;
  confidence_gap: { n: number; bull_mean: number; bear_mean: number; abs_gap: number };
  simulate_stats: Record<string, unknown> | null;
  per_agent_directional: Record<string, Record<string, AgentAccuracy>>;
}

export interface AgentAccuracy {
  accuracy: number | null;
  coverage: number;
  n_directional: number;
  n_correct: number;
}

export interface SummaryResponse {
  path: string | null;
  tickers: TickerReport[];
}

export interface RunRequest {
  market: "nasdaq" | "nepse";
  ticker: string;
  date: string;
  max_debate_rounds?: number | null;
  persist: boolean;
}

export interface RunInfo {
  run_id: string;
  params: Record<string, unknown>;
  status: "queued" | "running" | "done" | "error";
  started_at: number;
  n_events: number;
}
