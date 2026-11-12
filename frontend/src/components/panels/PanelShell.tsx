/* Shared shell for every per-ticker panel (Fundamentals, Technicals,
   Sentiment, Risk, and later Ownership). The whole point of this
   component is to make the two-layer pattern impossible to forget:
     top half   = raw numbers a trader expects to see
     bottom half = the agent that already read those numbers
   If a panel ever shipped WITHOUT the agent verdict, the Fuse-fellowship
   thesis — "agents are the researchers FOR the trader" — would quietly
   stop being visible. The shell enforces the pattern. */

import type { ReactNode } from "react";
import { Chip, Panel } from "../ui";

export type AgentState = "idle" | "loading" | "ready" | "error" | "skipped";

export function PanelShell({
  title,
  freshness,
  raw,
  agent,
  onInspect,
  canInspect = false,
}: {
  title: string;
  freshness?: string;
  raw: ReactNode;
  agent: ReactNode;
  onInspect?: () => void;
  canInspect?: boolean;
}) {
  return (
    <Panel
      title={title}
      right={
        <div className="flex items-center gap-2">
          {freshness && (
            <span className="text-[10px] text-faint uppercase tracking-widest">{freshness}</span>
          )}
          {canInspect && (
            <button
              type="button"
              onClick={onInspect}
              className="border border-hairline bg-panel-2 px-1.5 py-0.5 text-[10px] tracking-widest uppercase text-dim hover:border-data/60 hover:text-ink"
              title="View full prompt + structured output"
            >
              prompt ↗
            </button>
          )}
        </div>
      }
      className="flex min-h-[260px] flex-col"
    >
      <div className="flex-1 border-b border-hairline px-3 py-2">
        <div className="label-micro mb-1.5 text-faint">Raw numbers</div>
        <div className="space-y-0.5">{raw}</div>
      </div>
      <div className="px-3 py-2">
        <div className="label-micro mb-1.5 text-faint">Agent verdict</div>
        <div className="text-[12px] text-ink">{agent}</div>
      </div>
    </Panel>
  );
}

export function SignalChip({ signal, confidence }: { signal?: string | null; confidence?: number | null }) {
  if (!signal) return <Chip tone="neutral">pending</Chip>;
  const s = String(signal).toLowerCase();
  const tone: "bull" | "bear" | "amber" | "neutral" =
    s === "bullish" || s === "buy" || s === "approved"
      ? "bull"
      : s === "bearish" || s === "sell" || s === "vetoed"
        ? "bear"
        : s === "neutral" || s === "hold"
          ? "amber"
          : "neutral";
  const conf = confidence != null ? ` · ${(confidence * 100).toFixed(0)}%` : "";
  return (
    <Chip tone={tone}>
      {s}
      {conf}
    </Chip>
  );
}

export function AgentIdle({ hint }: { hint?: string }) {
  return (
    <div className="text-[11px] text-faint italic">
      {hint ?? "Agent has not run on this ticker yet. Click Analyze to call the committee."}
    </div>
  );
}
