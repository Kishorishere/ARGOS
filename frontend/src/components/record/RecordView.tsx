import type { JournalRecord } from "../../types/record";
import { fmtTs, num } from "../../lib/format";
import { Chip, Panel } from "../ui";
import AnalystsRow from "./AnalystsRow";
import ForecastPanel from "./ForecastPanel";
import { DebateTheater, JudgeCard } from "./DebateAndJudge";
import { DecisionHero, ErrorsList, RiskPanel } from "./RiskAndDecision";
import JsonDrawer from "./JsonDrawer";

function MetaBar({ r, attempt, of }: { r: JournalRecord; attempt: number; of: number }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border border-hairline bg-panel px-3 py-2 text-[11px] text-dim">
      <span className="text-[13px] font-bold text-ink">{r.ticker}</span>
      <span className="text-data">{r.date}</span>
      {of > 1 && <Chip tone="amber">attempt {attempt}/{of}</Chip>}
      <span>
        took <span className="text-ink tabular-nums">{num(r.duration_s, 1)}s</span>
      </span>
      <span>
        at <span className="text-ink">{fmtTs(r.attempted_at)}</span>
      </span>
      <span>
        ver <span className="text-ink">{r.argos_version ?? "—"}</span>
      </span>
      <span>
        bars <span className="text-ink tabular-nums">{r.ohlcv_summary?.rows ?? "—"}</span>
      </span>
      <span className="ml-auto flex gap-1.5">
        {(r.errors ?? []).length > 0 && <Chip tone="bear">{r.errors.length} errors</Chip>}
        {r.indicator_signals_fired?.length > 0 && (
          <Chip tone="data">{r.indicator_signals_fired.length} signals</Chip>
        )}
      </span>
    </div>
  );
}

function Composite({ r }: { r: JournalRecord }) {
  const c = r.indicator_composite;
  if (!c) return null;
  return (
    <Panel title="Indicator Composite">
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 p-3 text-[11px] sm:grid-cols-4">
        <div>
          <span className="label-micro">bull score</span>
          <div className="text-bull tabular-nums">{num(c.composite_bull_score, 3)}</div>
        </div>
        <div>
          <span className="label-micro">bear score</span>
          <div className="text-bear tabular-nums">{num(c.composite_bear_score, 3)}</div>
        </div>
        <div>
          <span className="label-micro">net bias</span>
          <div className={`tabular-nums ${c.net_bias_score >= 0 ? "text-bull" : "text-bear"}`}>
            {num(c.net_bias_score, 3)}
          </div>
        </div>
        <div>
          <span className="label-micro">regimes</span>
          <div className="text-ink">
            {c.trend_regime} / {c.vol_regime}
          </div>
        </div>
      </div>
    </Panel>
  );
}

export default function RecordView({
  record,
  attempt = 1,
  of = 1,
  showJson = true,
}: {
  record: JournalRecord;
  attempt?: number;
  of?: number;
  showJson?: boolean;
}) {
  return (
    <div className="space-y-3">
      <MetaBar r={record} attempt={attempt} of={of} />
      <DecisionHero d={record.final_decision} />
      <AnalystsRow signals={record.analyst_signals} />
      <ForecastPanel f={record.multi_horizon_forecast} />
      <DebateTheater debate={record.debate} transcript={record.debate_transcript ?? []} />
      <div className="grid gap-3 lg:grid-cols-2">
        <JudgeCard verdict={record.judge_verdict ?? null} />
        <RiskPanel risk={record.risk_verdict} />
      </div>
      <Composite r={record} />
      <ErrorsList errors={record.errors} />
      {record.error && (
        <div className="border-l-2 border-bear bg-bear/5 px-3 py-2 text-[11px] text-bear">{record.error}</div>
      )}
      {showJson && <JsonDrawer data={record} label={`${record.ticker}_${record.date}`} />}
    </div>
  );
}
