import type { Snapshot } from "../../types/snapshot";
import type { JournalRecord } from "../../types/record";
import { KV } from "../ui";
import { fmt, pct } from "../../lib/pick";
import { AgentIdle, PanelShell, SignalChip } from "./PanelShell";

interface Props {
  snapshot: Snapshot | null;
  latestJournal?: JournalRecord | null;
  onInspect?: () => void;
}

export default function FundamentalsPanel({ snapshot, latestJournal, onInspect }: Props) {
  const f = snapshot?.fundamentals ?? null;
  const freshness = snapshot?.freshness?.fundamentals_latest_quarter
    ? `Q ${snapshot.freshness.fundamentals_latest_quarter}`
    : undefined;

  const raw =
    !f || f.error ? (
      <div className="text-[11px] text-faint italic">
        {f?.error ?? "No cached fundamentals for this ticker. Phase C wires EDGAR / Finnhub."}
      </div>
    ) : (
      <>
        <KV k="P/E" v={fmt(f.pe)} />
        <KV k="P/B" v={fmt(f.pb)} />
        <KV k="ROE" v={pct(f.roe_pct)} />
        <KV k="ROA" v={pct(f.roa_pct)} />
        <KV k="EPS" v={fmt(f.eps)} />
        <KV k="Book value" v={fmt(f.book_value)} />
      </>
    );

  const agentSignal = latestJournal?.analyst_signals?.fundamentals;
  const agent = agentSignal ? (
    <div className="space-y-1.5">
      <SignalChip signal={agentSignal.signal} confidence={agentSignal.confidence} />
      {agentSignal.key_factors && agentSignal.key_factors.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-dim">
          {agentSignal.key_factors.slice(0, 3).map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      {agentSignal.reasoning && (
        <div className="line-clamp-3 text-[11px] text-dim italic">"{agentSignal.reasoning}"</div>
      )}
    </div>
  ) : (
    <AgentIdle />
  );

  return (
    <PanelShell
      title="Fundamentals"
      freshness={freshness}
      raw={raw}
      agent={agent}
      onInspect={onInspect}
      canInspect={!!agentSignal && !!onInspect}
    />
  );
}
