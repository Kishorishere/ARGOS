/* Ownership panel — insider Form 4 events in the raw layer, Fundamentals
   analyst verdict in the agent layer (since the committee's Fundamentals
   analyst is the one that already reasons over ownership-shaped signals).

   13F holder inversion is intentionally deferred to Phase C.2 — the
   panel surfaces the pending state honestly rather than hiding the
   feature. */

import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { OwnershipResponse } from "../../types/snapshot";
import type { JournalRecord } from "../../types/record";
import { Chip, KV } from "../ui";
import { fmt } from "../../lib/pick";
import { AgentIdle, PanelShell, SignalChip } from "./PanelShell";

interface Props {
  ticker: string;
  latestJournal?: JournalRecord | null;
  onInspect?: () => void;
}

export default function OwnershipPanel({ ticker, latestJournal, onInspect }: Props) {
  const [data, setData] = useState<OwnershipResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(null);
    api
      .ownership(ticker)
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [ticker]);

  const form4 = data?.form4 ?? [];
  const buys = form4.filter((e) => e.direction === "A");
  const sells = form4.filter((e) => e.direction === "D");

  const raw =
    err ? (
      <div className="text-[11px] text-bear">{err}</div>
    ) : !data ? (
      <div className="text-[11px] text-faint italic">loading EDGAR…</div>
    ) : data.error ? (
      <div className="text-[11px] text-faint italic">{data.error}</div>
    ) : (
      <>
        <KV
          k="Insiders buy"
          v={
            <span className="space-x-1">
              <Chip tone={buys.length ? "bull" : "neutral"}>{buys.length}</Chip>
              <span className="text-dim">{sumShares(buys)}</span>
            </span>
          }
        />
        <KV
          k="Insiders sell"
          v={
            <span className="space-x-1">
              <Chip tone={sells.length ? "bear" : "neutral"}>{sells.length}</Chip>
              <span className="text-dim">{sumShares(sells)}</span>
            </span>
          }
        />
        <KV k="CIK" v={<span className="text-dim tabular-nums">{data.cik ?? "—"}</span>} />
        <KV
          k="Top holders"
          v={
            data.holders.length ? (
              <span className="text-dim">{data.holders.length} of 13F</span>
            ) : (
              <span className="text-faint italic text-[10px]">
                {data.holders_note ?? "—"}
              </span>
            )
          }
        />
        {data.holders.length > 0 && (
          <div className="pt-1">
            <div className="label-micro mb-0.5 text-faint text-[9px]">top 3</div>
            <ul className="space-y-0.5 text-[11px]">
              {data.holders.slice(0, 3).map((h, i) => (
                <li key={`${h.filer_name}-${i}`} className="flex gap-1.5 text-dim">
                  <span className="truncate text-ink" title={h.filer_name}>
                    {shortName(h.filer_name)}
                  </span>
                  <span className="ml-auto tabular-nums text-faint">
                    {h.pct_outstanding != null
                      ? `${h.pct_outstanding.toFixed(2)}%`
                      : h.shares != null
                        ? `${Math.round(h.shares / 1_000_000)}M sh`
                        : "—"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {form4.length > 0 && (
          <div className="pt-1">
            <div className="label-micro mb-0.5 text-faint text-[9px]">latest</div>
            <ul className="space-y-0.5 text-[11px]">
              {form4.slice(0, 3).map((e, i) => (
                <li key={`${e.accession}-${i}`} className="flex gap-1.5 text-dim">
                  <Chip tone={e.direction === "A" ? "bull" : e.direction === "D" ? "bear" : "neutral"}>
                    {e.direction ?? "?"}
                  </Chip>
                  <span className="truncate">
                    <span className="text-ink">{shortName(e.reporting_owner)}</span>{" "}
                    <span className="text-faint">· {fmt(e.transaction_shares, 0)}sh</span>
                  </span>
                  <span className="ml-auto text-faint tabular-nums">{e.transaction_date ?? e.filed_at}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </>
    );

  const agentSignal = latestJournal?.analyst_signals?.fundamentals;
  const agent = agentSignal ? (
    <div className="space-y-1.5">
      <SignalChip signal={agentSignal.signal} confidence={agentSignal.confidence} />
      <div className="text-[10px] uppercase tracking-widest text-faint">fundamentals agent reads ownership</div>
      {agentSignal.reasoning && (
        <div className="line-clamp-2 text-[11px] text-dim italic">"{agentSignal.reasoning}"</div>
      )}
    </div>
  ) : (
    <AgentIdle hint="Fundamentals agent reads this panel's ownership signals when triggered." />
  );

  return (
    <PanelShell
      title="Ownership"
      raw={raw}
      agent={agent}
      onInspect={onInspect}
      canInspect={!!agentSignal && !!onInspect}
    />
  );
}

function shortName(name: string | null | undefined): string {
  if (!name) return "unknown";
  return name.length > 22 ? name.slice(0, 20) + "…" : name;
}

function sumShares(events: { transaction_shares: number | null }[]): string {
  const total = events.reduce((s, e) => s + (e.transaction_shares ?? 0), 0);
  if (!total) return "—";
  return `${Math.round(total).toLocaleString()}sh`;
}
