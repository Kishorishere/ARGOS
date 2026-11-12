import type { Snapshot } from "../../types/snapshot";
import type { JournalRecord } from "../../types/record";
import { Chip, KV } from "../ui";
import { fmt } from "../../lib/pick";
import { AgentIdle, PanelShell, SignalChip } from "./PanelShell";

interface Props {
  snapshot: Snapshot | null;
  latestJournal?: JournalRecord | null;
  onInspect?: () => void;
}

export default function RiskPanel({ snapshot, latestJournal, onInspect }: Props) {
  const p = snapshot?.portfolio ?? null;
  const pos = p?.position;
  const held = p?.held ?? false;

  const unrealized =
    pos && pos.avg_price != null && pos.market_price != null && pos.quantity != null
      ? (pos.market_price - pos.avg_price) * pos.quantity
      : null;

  const raw = (
    <>
      <KV
        k="Position"
        v={
          held && pos ? (
            <span>
              {pos.quantity} @ {fmt(pos.avg_price)}
            </span>
          ) : (
            <Chip tone="neutral">flat</Chip>
          )
        }
      />
      {held && pos && (
        <>
          <KV k="Market price" v={fmt(pos.market_price)} />
          <KV k="Unrealized" v={unrealized != null ? fmt(unrealized) : "—"} />
          <KV k="Sector" v={pos.sector ?? "—"} />
        </>
      )}
      <KV
        k="Portfolio ctx"
        v={
          p?.portfolio_context && !("error" in p.portfolio_context)
            ? <span className="text-dim">{Object.keys(p.portfolio_context).length} keys</span>
            : <span className="text-faint italic">none</span>
        }
      />
    </>
  );

  const risk = latestJournal?.risk_verdict;
  const agent = risk ? (
    <div className="space-y-1.5">
      <SignalChip signal={risk.veto ? "vetoed" : "approved"} />
      {risk.risk_factors && risk.risk_factors.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-dim">
          {risk.risk_factors.slice(0, 3).map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      {risk.reasoning && (
        <div className="line-clamp-3 text-[11px] text-dim italic">"{risk.reasoning}"</div>
      )}
      {typeof risk.max_position_size === "number" && (
        <div className="text-[10px] uppercase tracking-widest text-faint">
          max size {(risk.max_position_size * 100).toFixed(1)}%
        </div>
      )}
    </div>
  ) : (
    <AgentIdle hint="Risk Manager runs after the debate. Analyze to produce a verdict." />
  );

  return (
    <PanelShell
      title="Risk"
      raw={raw}
      agent={agent}
      onInspect={onInspect}
      canInspect={!!risk && !!onInspect}
    />
  );
}
