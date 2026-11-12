import { useState } from "react";
import type { AnalystSignals, FiredSignal, KeyFactorSummary } from "../../types/record";
import { num, signalTone } from "../../lib/format";
import { Chip, ConfidenceBar, Empty, Panel } from "../ui";

const AGENTS: { key: keyof AnalystSignals; label: string }[] = [
  { key: "fundamentals", label: "Fundamentals" },
  { key: "sentiment", label: "Sentiment" },
  { key: "technicals", label: "Technicals" },
];

function Snapshot({ sig }: { sig: KeyFactorSummary }) {
  const snap = sig.indicator_snapshot;
  if (!snap) return null;
  const fired = snap.fired_signals ?? [];
  const groups = [
    ["momentum", snap.momentum],
    ["trend", snap.trend],
    ["volatility", snap.volatility],
    ["volume", snap.volume],
    ["structure", snap.structure],
    ["candles", snap.candles],
    ["divergences", snap.divergences],
  ] as const;
  return (
    <div className="border-t border-hairline px-3 py-2">
      <div className="mb-2 grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <span className="label-micro">bull</span>
          <div className="text-bull tabular-nums">{num(snap.composite_bull_score, 3)}</div>
        </div>
        <div>
          <span className="label-micro">bear</span>
          <div className="text-bear tabular-nums">{num(snap.composite_bear_score, 3)}</div>
        </div>
        <div>
          <span className="label-micro">net bias</span>
          <div className={`tabular-nums ${snap.net_bias_score >= 0 ? "text-bull" : "text-bear"}`}>
            {num(snap.net_bias_score, 3)}
          </div>
        </div>
      </div>
      {fired.length > 0 && (
        <div className="space-y-1">
          {fired.map((f, i) => (
            <FiredRow key={i} f={f} />
          ))}
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-1">
        {groups
          .filter(([, g]) => g && Array.isArray(g.signals) && g.signals.length > 0)
          .map(([name, g]) => (
            <Chip key={name} tone="data">
              {name}: {g!.signals!.length}
            </Chip>
          ))}
      </div>
    </div>
  );
}

function FiredRow({ f }: { f: FiredSignal }) {
  const tone =
    f.direction === "bearish" ? "text-bear" : f.direction === "bullish" ? "text-bull" : "text-amber";
  return (
    <div className="flex items-baseline gap-2 text-[11px]">
      <span className={tone}>{f.direction === "bearish" ? "▼" : f.direction === "bullish" ? "▲" : "■"}</span>
      <span className="text-ink">{f.indicator}</span>
      <span className="text-dim tabular-nums">{String(f.value)}</span>
      <span className="ml-auto text-faint tabular-nums">w {num(f.weight, 2)}</span>
    </div>
  );
}

function AnalystCard({ label, sig }: { label: string; sig: KeyFactorSummary | null }) {
  const [open, setOpen] = useState(false);
  if (!sig) {
    return (
      <Panel title={label}>
        <Empty>no signal</Empty>
      </Panel>
    );
  }
  return (
    <Panel
      title={label}
      right={
        <span className={`border px-1.5 py-px text-[10px] tracking-wider uppercase ${signalTone(sig.signal)}`}>
          {sig.signal}
        </span>
      }
    >
      <div className="space-y-2 px-3 py-2">
        <ConfidenceBar value={sig.confidence} />
        <ul className="space-y-0.5">
          {sig.key_factors.slice(0, 5).map((f, i) => (
            <li key={i} className="flex gap-1.5 text-[11px] leading-snug">
              <span className="text-faint">›</span>
              <span className="text-ink">{f}</span>
            </li>
          ))}
        </ul>
        <button
          onClick={() => setOpen((o) => !o)}
          className="text-[10px] tracking-widest text-data uppercase hover:underline"
        >
          {open ? "− hide reasoning" : "+ reasoning"}
        </button>
        {open && <p className="text-[11px] leading-relaxed text-dim whitespace-pre-wrap">{sig.reasoning}</p>}
      </div>
      {open && <Snapshot sig={sig} />}
    </Panel>
  );
}

export default function AnalystsRow({ signals }: { signals: AnalystSignals | null }) {
  if (!signals) {
    return (
      <Panel title="Analyst Signals">
        <Empty>no analyst signals in record</Empty>
      </Panel>
    );
  }
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {AGENTS.map((a) => (
        <AnalystCard key={a.key} label={a.label} sig={signals[a.key]} />
      ))}
    </div>
  );
}
