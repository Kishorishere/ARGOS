import type { Snapshot } from "../../types/snapshot";
import type { JournalRecord } from "../../types/record";
import { KV } from "../ui";
import { fmt, num, pick } from "../../lib/pick";
import { AgentIdle, PanelShell, SignalChip } from "./PanelShell";

interface Props {
  snapshot: Snapshot | null;
  latestJournal?: JournalRecord | null;
  onInspect?: () => void;
}

/* The indicator snapshot shape (argos.indicators.schemas.IndicatorSnapshot)
   is deep and version-prone, so we pull named fields with `pick` rather
   than via a strict interface. If the server changes a key, the panel
   degrades to "—" instead of blanking out. */
export default function TechnicalsPanel({ snapshot, latestJournal, onInspect }: Props) {
  const snap = snapshot?.indicators ?? null;
  const price = snapshot?.price;

  const rsi = num(pick(snap, "momentum.rsi_14"));
  const macdHist = num(pick(snap, "momentum.macd_hist"));
  const bbPctB = num(pick(snap, "volatility.bollinger_pct_b"));
  const atr = num(pick(snap, "volatility.atr_14"));
  const vol20 = num(pick(snap, "volatility.vol_20d_ann_pct"));
  const ma20 = num(pick(snap, "trend.ma_20"));
  const ma50 = num(pick(snap, "trend.ma_50"));
  const ma200 = num(pick(snap, "trend.ma_200"));
  const trendRegime = pick<string>(snap, "trend.regime");
  const volRegime = pick<string>(snap, "volatility.vol_regime");
  const netBias = num(pick(snap, "net_bias_score"));

  const raw =
    snap && !snap.error ? (
      <>
        <KV k="RSI(14)" v={fmt(rsi, 1)} />
        <KV k="Bollinger %B" v={fmt(bbPctB, 2)} />
        <KV k="MACD hist" v={fmt(macdHist, 2)} />
        <KV k="ATR(14)" v={fmt(atr, 2)} />
        <KV k="Vol 20d (ann)" v={vol20 != null ? `${vol20.toFixed(1)}%` : "—"} />
        <KV k="MA 20 / 50 / 200" v={`${fmt(ma20, 0)} / ${fmt(ma50, 0)} / ${fmt(ma200, 0)}`} />
        <KV
          k="Regime"
          v={
            <span className="space-x-1">
              <span className="text-dim">{trendRegime ?? "?"}</span>
              <span className="text-faint">·</span>
              <span className="text-dim">{volRegime ?? "?"}</span>
            </span>
          }
        />
        {netBias != null && <KV k="Net bias" v={fmt(netBias, 2)} />}
      </>
    ) : (
      <div className="text-[11px] text-faint italic">
        {snap?.error ?? "Indicator snapshot unavailable."}
      </div>
    );

  const agentSignal = latestJournal?.analyst_signals?.technicals;
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
    <AgentIdle hint={price?.last != null ? "Indicators are computed. Run Analyze to see the committee's technical read." : undefined} />
  );

  return (
    <PanelShell
      title="Technicals"
      raw={raw}
      agent={agent}
      onInspect={onInspect}
      canInspect={!!agentSignal && !!onInspect}
    />
  );
}
