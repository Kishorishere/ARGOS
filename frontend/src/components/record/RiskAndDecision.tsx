import type { FinalDecision, RiskVerdict } from "../../types/record";
import { num, pct } from "../../lib/format";
import { Chip, ConfidenceBar, Empty, KV, Panel } from "../ui";

export function RiskPanel({ risk }: { risk: RiskVerdict | null }) {
  if (!risk) {
    return (
      <Panel title="Risk Manager">
        <Empty>no risk verdict in record</Empty>
      </Panel>
    );
  }
  return (
    <Panel
      title="Risk Manager"
      right={
        <span className={`text-[11px] tracking-widest uppercase ${risk.veto ? "text-bear" : "text-bull"}`}>
          {risk.veto ? "⊘ vetoed" : "✓ allowed"} · {risk.veto_count}/{risk.veto_votes.length} veto votes
        </span>
      }
    >
      <div className="p-3">
        <div className="mb-2 grid grid-cols-2 gap-1 sm:grid-cols-4">
          {risk.veto_votes.map((v) => (
            <div
              key={v.name}
              title={v.reason}
              className={`border px-2 py-1 ${
                v.veto ? "border-bear/50 bg-bear/10" : "border-hairline bg-panel-2"
              }`}
            >
              <div className="flex items-center justify-between gap-1">
                <span className={`text-[10px] tracking-wider uppercase ${v.veto ? "text-bear" : "text-dim"}`}>
                  {v.name}
                </span>
                <span className={`text-[10px] ${v.veto ? "text-bear" : "text-bull"}`}>
                  {v.veto ? "veto" : "allow"}
                </span>
              </div>
            </div>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <KV k="max position" v={pct(risk.max_position_size, 0)} />
            {risk.risk_factors?.map((f, i) => (
              <div key={i} className="flex gap-1.5 border-b border-hairline/60 py-1 text-[11px] last:border-0">
                <span className="text-amber">•</span>
                <span className="text-dim">{f}</span>
              </div>
            ))}
          </div>
          <p className="text-[11px] leading-relaxed text-dim">{risk.reasoning}</p>
        </div>
      </div>
    </Panel>
  );
}

function ExitPlan({ d }: { d: FinalDecision }) {
  const items = [
    { k: "stop loss", v: d.stop_loss_pct, fmt: (x: number) => pct(x, 2) },
    { k: "take profit", v: d.take_profit_pct, fmt: (x: number) => pct(x, 2) },
    { k: "trailing stop", v: d.trailing_stop_atr, fmt: (x: number) => `${num(x, 1)} ATR` },
    { k: "time stop", v: d.time_stop_bars, fmt: (x: number) => `${x} bars` },
  ];
  return (
    <div className="grid grid-cols-2 gap-1 sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.k} className="border border-hairline bg-panel-2 px-2 py-1.5">
          <div className="label-micro">{it.k}</div>
          <div className={`text-[13px] tabular-nums ${it.v === null || it.v === undefined ? "text-faint" : "text-data"}`}>
            {it.v === null || it.v === undefined ? "—" : it.fmt(it.v)}
          </div>
        </div>
      ))}
    </div>
  );
}

function Sizing({ d }: { d: FinalDecision }) {
  const st = d.sizing_trace;
  if (!st) {
    return (
      <div className="text-[11px] text-faint">no sizing trace {st === null ? "(null)" : "(absent)"}</div>
    );
  }
  if (st.reason) {
    return (
      <div className="border border-hairline bg-panel-2 px-2 py-1.5 text-[11px] text-dim">
        <span className="label-micro">kelly sizing</span>
        <div>
          size <span className="text-ink tabular-nums">{pct(st.size, 1)}</span> — {st.reason}
        </div>
      </div>
    );
  }
  const rows: [string, string][] = [
    ["size", pct(st.size, 2)],
    ["edge", st.edge_pct !== undefined ? `${num(st.edge_pct, 3)}%` : "—"],
    ["variance", st.variance_pct2 !== undefined ? num(st.variance_pct2, 3) : "—"],
    ["kelly signed", st.kelly_signed !== undefined ? num(st.kelly_signed, 3) : "—"],
    ["conviction ×", st.conviction_mult !== undefined ? num(st.conviction_mult, 4) : "—"],
    ["agent agreement", st.agent_agreement !== undefined ? pct(st.agent_agreement, 0) : "—"],
    ["atr used", st.atr_pct_used !== undefined ? `${num(st.atr_pct_used, 3)}%` : "—"],
    ["risk cap", st.risk_cap !== undefined ? pct(st.risk_cap, 0) : "—"],
    ["hard cap", st.hard_cap !== undefined ? pct(st.hard_cap, 0) : "—"],
    ["fraction of kelly", st.fraction_of_kelly !== undefined ? pct(st.fraction_of_kelly, 0) : "—"],
    ["dd scale", st.dd_scale !== undefined ? num(st.dd_scale, 2) : "—"],
    ["dd status", st.dd_status ?? "—"],
  ];
  return (
    <div>
      <div className="grid grid-cols-2 gap-x-5 sm:grid-cols-3">
        {rows.map(([k, v]) => (
          <KV key={k} k={k} v={v} />
        ))}
      </div>
      {st.dd_reason && <div className="mt-1 text-[10px] text-faint">{st.dd_reason}</div>}
      {d.sizing_llm_original !== null && d.sizing_llm_original !== undefined && (
        <div className="mt-1 text-[10px] text-faint">
          llm original: {pct(d.sizing_llm_original, 1)} → kelly override
        </div>
      )}
    </div>
  );
}

export function DecisionHero({ d }: { d: FinalDecision | null }) {
  if (!d) {
    return (
      <Panel title="Final Decision">
        <Empty>no final decision in record</Empty>
      </Panel>
    );
  }
  const tone =
    d.action === "buy" ? "text-bull" : d.action === "sell" ? "text-bear" : "text-amber";
  const border =
    d.action === "buy" ? "border-bull/40" : d.action === "sell" ? "border-bear/40" : "border-amber/40";
  return (
    <section className={`border ${border} bg-panel`}>
      <header className="flex flex-wrap items-center gap-4 border-b border-hairline px-4 py-3">
        <div className="flex items-baseline gap-3">
          <span className="label-micro">final decision</span>
          <span className={`text-[26px] leading-none font-bold tracking-[0.2em] uppercase ${tone}`}>
            {d.action}
          </span>
        </div>
        <div className="w-40">
          <span className="label-micro">confidence</span>
          <ConfidenceBar value={d.confidence} />
        </div>
        <div className="ml-auto text-right">
          <span className="label-micro">position size</span>
          <div className={`text-[20px] leading-none tabular-nums ${tone}`}>{pct(d.position_size, 2)}</div>
        </div>
      </header>
      <div className="space-y-3 px-4 py-3">
        <ExitPlan d={d} />
        <p className="text-[12px] leading-relaxed text-ink">{d.rationale}</p>
        {d.dissent_notes && (
          <p className="text-[11px] leading-relaxed text-dim">
            <span className="text-amber">dissent: </span>
            {d.dissent_notes}
          </p>
        )}
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="label-micro">kelly sizing trace</span>
            <Chip tone="data">U2</Chip>
          </div>
          <Sizing d={d} />
        </div>
      </div>
    </section>
  );
}

export function ErrorsList({ errors }: { errors: string[] | undefined }) {
  if (!errors || errors.length === 0) return null;
  return (
    <Panel title={`Errors (${errors.length})`}>
      <ul className="space-y-1 p-3">
        {errors.map((e, i) => (
          <li key={i} className="border-l-2 border-bear bg-bear/5 px-2 py-1 text-[11px] text-bear">
            {e}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
