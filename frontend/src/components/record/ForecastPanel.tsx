import type { EnsembleHorizon, HorizonForecast, MultiHorizonForecast } from "../../types/record";
import { num, pct } from "../../lib/format";
import { Empty, Panel } from "../ui";

const HORIZONS = ["1d", "1w", "1m"] as const;
const ARROW = { up: "▲", down: "▼", flat: "■" } as const;

function Band({ h }: { h: HorizonForecast }) {
  const min = Math.min(h.p10_return_pct, 0);
  const max = Math.max(h.p90_return_pct, 0);
  const span = max - min || 1;
  const x = (v: number) => ((v - min) / span) * 100;
  const zeroX = x(0);
  const tone =
    h.direction === "up" ? "var(--color-bull)" : h.direction === "down" ? "var(--color-bear)" : "var(--color-amber)";
  return (
    <svg viewBox="0 0 100 22" className="h-7 w-full" preserveAspectRatio="none" aria-hidden>
      <line x1="0" y1="16" x2="100" y2="16" stroke="var(--color-hairline-2)" strokeWidth="0.5" />
      <line x1={zeroX} y1="4" x2={zeroX} y2="20" stroke="var(--color-faint)" strokeWidth="0.5" strokeDasharray="2 2" />
      <rect
        x={x(h.p10_return_pct)}
        y="9"
        width={Math.max(x(h.p90_return_pct) - x(h.p10_return_pct), 0.5)}
        height="6"
        fill={tone}
        opacity="0.18"
      />
      <line
        x1={x(h.p50_return_pct)}
        y1="6"
        x2={x(h.p50_return_pct)}
        y2="18"
        stroke={tone}
        strokeWidth="1.2"
      />
      <circle cx={x(h.expected_return_pct)} cy="12" r="1.6" fill={tone} />
    </svg>
  );
}

function Gauge({ p }: { p: number }) {
  const v = Math.max(0, Math.min(1, p));
  const R = 14;
  const CIRC = Math.PI * R; // half circle
  return (
    <svg viewBox="0 0 40 24" className="h-9 w-11" aria-hidden>
      <path
        d="M 6 20 A 14 14 0 0 1 34 20"
        fill="none"
        stroke="var(--color-panel-2)"
        strokeWidth="4"
      />
      <path
        d="M 6 20 A 14 14 0 0 1 34 20"
        fill="none"
        stroke={v >= 0.5 ? "var(--color-bull)" : "var(--color-bear)"}
        strokeWidth="4"
        strokeDasharray={`${CIRC * v} ${CIRC}`}
        strokeLinecap="butt"
      />
      <text x="20" y="19" textAnchor="middle" fontSize="8" fill="var(--color-ink)">
        {(v * 100).toFixed(0)}
      </text>
    </svg>
  );
}

function Waterfall({ e }: { e: EnsembleHorizon }) {
  const contribs = [...(e.contributions ?? [])].sort((a, b) => Math.abs(b.log_odds) - Math.abs(a.log_odds));
  if (contribs.length === 0) return null;
  const peak = Math.max(...contribs.map((c) => Math.abs(c.log_odds)), 0.01);
  return (
    <div className="mt-2 space-y-1">
      <div className="label-micro">driver contribution (log-odds)</div>
      {contribs.map((c, i) => {
        const w = (Math.abs(c.log_odds) / peak) * 50;
        const pos = c.log_odds >= 0;
        return (
          <div key={i} className="flex items-center gap-2 text-[10px]">
            <span className="w-32 shrink-0 truncate text-dim" title={c.note || c.source}>
              {c.source}
            </span>
            <div className="relative h-2.5 flex-1 bg-panel-2">
              <div className="absolute top-0 bottom-0 left-1/2 w-px bg-hairline-2" />
              <div
                className={`absolute top-0 bottom-0 ${pos ? "left-1/2 bg-bull" : "right-1/2 bg-bear"}`}
                style={{ width: `${w}%` }}
              />
            </div>
            <span className={`w-12 text-right tabular-nums ${pos ? "text-bull" : "text-bear"}`}>
              {c.log_odds >= 0 ? "+" : ""}
              {c.log_odds.toFixed(3)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function HorizonCard({ h, e }: { h: HorizonForecast; e?: EnsembleHorizon }) {
  const tone =
    h.direction === "up" ? "text-bull" : h.direction === "down" ? "text-bear" : "text-amber";
  return (
    <div className="panel p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[12px] font-bold tracking-[0.2em] text-ink uppercase">{h.horizon}</span>
        <span className={`text-[13px] ${tone}`}>
          {ARROW[h.direction]} {h.direction}
        </span>
      </div>
      <div className="mb-1 flex items-center gap-2">
        <Gauge p={e?.probability_up ?? h.probability_up} />
        <div className="flex-1 text-[10px] leading-relaxed text-dim">
          <div className="flex justify-between">
            <span>p10</span>
            <span>p50</span>
            <span>p90</span>
          </div>
          <div className="flex justify-between text-ink tabular-nums">
            <span>{num(h.p10_return_pct, 2)}</span>
            <span>{num(h.p50_return_pct, 2)}</span>
            <span>{num(h.p90_return_pct, 2)}</span>
          </div>
          <div className="mt-1 flex justify-between">
            <span>conf</span>
            <span className="text-data tabular-nums">{pct(h.confidence, 0)}</span>
          </div>
        </div>
      </div>
      <Band h={h} />
      {e && <Waterfall e={e} />}
    </div>
  );
}

export default function ForecastPanel({ f }: { f: MultiHorizonForecast | null }) {
  if (!f) {
    return (
      <Panel title="Multi-Horizon Forecast">
        <Empty>no forecast in record</Empty>
      </Panel>
    );
  }
  return (
    <Panel
      title="Multi-Horizon Forecast"
      right={
        <span className="text-[11px] text-dim tabular-nums">
          {f.symbol} · {f.as_of_date} · last{" "}
          <span className="text-data">{num(f.last_close, 2)}</span>
        </span>
      }
    >
      <div className="grid gap-3 p-3 md:grid-cols-3">
        {HORIZONS.map((hz) => {
          const h = f.horizons[hz];
          if (!h) return null;
          return <HorizonCard key={hz} h={h} e={f.ensemble_by_horizon[hz]} />;
        })}
      </div>
    </Panel>
  );
}
