/* Analytics view — the paper's trust panel in one page.

   Three sections:
     1. Reliability curve for committee confidence (per horizon)
     2. Abstention panel — per-ticker hold rate, mean confidence on
        HOLDs vs trades. The selective-prediction research angle.
     3. Override log — the last N user disagreements with the committee.

   This is the view the fellowship reviewer opens to see whether the
   system is being honest with itself. */

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type {
  AbstentionResponse,
  CalibrationResponse,
  OverrideRow,
} from "../types/snapshot";
import { Chip, Empty, Panel } from "../components/ui";
import { fmt, pct } from "../lib/pick";

export default function Analytics() {
  const [curve, setCurve] = useState<CalibrationResponse | null>(null);
  const [abst, setAbst] = useState<AbstentionResponse | null>(null);
  const [overrides, setOverrides] = useState<OverrideRow[] | null>(null);
  const [horizon, setHorizon] = useState(5);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.calibration(horizon).then((c) => alive && setCurve(c)).catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [horizon]);

  useEffect(() => {
    let alive = true;
    api.abstention().then((a) => alive && setAbst(a)).catch(() => {});
    api.overrides({ limit: 50 }).then((r) => alive && setOverrides(r.overrides)).catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="space-y-4 p-4">
      {err && <div className="text-[11px] text-bear">{err}</div>}

      {/* Reliability */}
      <Panel
        title="Committee confidence — reliability curve"
        right={
          <div className="flex items-center gap-1">
            {([1, 5, 20] as const).map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setHorizon(h)}
                className={`border px-2 py-0.5 text-[10px] tracking-widest uppercase ${
                  horizon === h
                    ? "border-data/60 bg-data/10 text-ink"
                    : "border-hairline text-dim hover:text-ink"
                }`}
              >
                h={h}
              </button>
            ))}
          </div>
        }
      >
        <div className="px-3 py-3">
          {!curve && <Empty>loading reliability curve…</Empty>}
          {curve && (
            <div className="space-y-2">
              <div className="text-[11px] text-faint">
                n_scored = <span className="text-ink tabular-nums">{curve.n_scored}</span> · overall accuracy ={" "}
                <span className="text-ink tabular-nums">{curve.overall_accuracy != null ? pct(curve.overall_accuracy * 100) : "—"}</span>
              </div>
              <ReliabilityBars curve={curve} />
              <div className="text-[11px] text-faint">
                Bars show empirical accuracy by confidence bucket. A perfectly calibrated system aligns the top of each bar with the midpoint of its bucket (so 0.6–0.7 confidence → ~65% accuracy).
              </div>
            </div>
          )}
        </div>
      </Panel>

      {/* Abstention */}
      <Panel
        title="Abstention panel — selective prediction"
        right={abst ? <Chip tone="data">{abst.n_tickers} tickers · {abst.source}</Chip> : null}
      >
        <div className="px-3 py-3">
          {!abst && <Empty>loading abstention stats…</Empty>}
          {abst && abst.tickers.length === 0 && <Empty>no reference-run data yet.</Empty>}
          {abst && abst.tickers.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-full text-[12px]">
                <thead className="label-micro text-faint">
                  <tr>
                    <th className="px-2 py-1 text-left">Ticker</th>
                    <th className="px-2 py-1 text-right">n</th>
                    <th className="px-2 py-1 text-right">HOLD</th>
                    <th className="px-2 py-1 text-right">BUY</th>
                    <th className="px-2 py-1 text-right">SELL</th>
                    <th className="px-2 py-1 text-right">HOLD rate</th>
                    <th className="px-2 py-1 text-right">conf on HOLD</th>
                    <th className="px-2 py-1 text-right">conf on trade</th>
                  </tr>
                </thead>
                <tbody>
                  {abst.tickers.map((r) => (
                    <tr key={r.ticker} className="border-t border-hairline/60">
                      <td className="px-2 py-1 text-ink">{r.ticker}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{r.n}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{r.n_hold}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{r.n_buy}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{r.n_sell}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{pct(r.hold_rate * 100)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{fmt(r.mean_conf_on_hold)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{fmt(r.mean_conf_on_trade)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Panel>

      {/* Overrides */}
      <Panel
        title="Override log — user disagreements"
        right={overrides ? <Chip tone="neutral">{overrides.length} entries</Chip> : null}
      >
        <div className="px-3 py-3">
          {!overrides && <Empty>loading…</Empty>}
          {overrides && overrides.length === 0 && (
            <Empty>
              no overrides yet. the "I disagree" button on the committee band logs here.
            </Empty>
          )}
          {overrides && overrides.length > 0 && (
            <ul className="divide-y divide-hairline">
              {overrides.map((o) => (
                <li key={o.id} className="py-2 text-[12px]">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-data">{o.ticker}</span>
                    <span className="text-faint">{o.as_of}</span>
                    <Chip tone="neutral">committee: {o.committee_action ?? "—"}</Chip>
                    <Chip tone="amber">user: {o.user_action}</Chip>
                    <span className="ml-auto text-[10px] uppercase tracking-widest text-faint">
                      {o.logged_at.replace("T", " ").replace("+00:00", "Z")}
                    </span>
                  </div>
                  <div className="pt-1 text-[11px] italic text-dim">"{o.user_reason}"</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>
    </div>
  );
}

function ReliabilityBars({ curve }: { curve: CalibrationResponse }) {
  return (
    <div className="flex items-end gap-1" style={{ height: 160 }}>
      {curve.bins.map((b) => {
        const mid = (b.confidence_bin_lo + b.confidence_bin_hi) / 2;
        const acc = b.empirical_accuracy;
        const h = acc != null ? Math.max(2, acc * 100) : 0;
        const gap = acc != null ? acc - mid : null;
        const tone =
          gap == null
            ? "bg-panel-2"
            : Math.abs(gap) <= 0.05
              ? "bg-bull/60"
              : Math.abs(gap) <= 0.15
                ? "bg-amber/60"
                : "bg-bear/60";
        return (
          <div key={b.confidence_bin_lo} className="flex-1" style={{ alignSelf: "flex-end", height: "100%" }}>
            <div className="flex h-full flex-col justify-end">
              <div className={`${tone} w-full`} style={{ height: `${h}%` }} title={`bucket ${b.confidence_bin_lo}-${b.confidence_bin_hi} · n=${b.n} · acc=${acc != null ? (acc * 100).toFixed(1) + '%' : '—'}`} />
              <div className="pt-0.5 text-center text-[9px] text-faint">
                {Math.round(b.confidence_bin_lo * 100)}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
