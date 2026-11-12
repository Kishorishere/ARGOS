import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { DEFAULT_WATCHLIST } from "../lib/watchlist";
import { fmt, num, pct, pick } from "../lib/pick";
import type { DecisionRow, Snapshot, Trigger } from "../types/snapshot";
import { Chip, Panel } from "../components/ui";
import StageBadge from "../components/StageBadge";

interface Row {
  ticker: string;
  snapshot: Snapshot | null;
  lastDecision: DecisionRow | null;
  triggers: Trigger[] | null;
  error?: string;
}

export default function Dashboard() {
  const [rows, setRows] = useState<Row[]>(
    DEFAULT_WATCHLIST.map((t) => ({ ticker: t, snapshot: null, lastDecision: null, triggers: null })),
  );

  useEffect(() => {
    let alive = true;
    DEFAULT_WATCHLIST.forEach((ticker, idx) => {
      api
        .snapshot(ticker)
        .then((s) => {
          if (!alive) return;
          setRows((r) => {
            const copy = r.slice();
            copy[idx] = { ...copy[idx], snapshot: s };
            return copy;
          });
        })
        .catch((e: Error) => {
          if (!alive) return;
          setRows((r) => {
            const copy = r.slice();
            copy[idx] = { ...copy[idx], error: e.message };
            return copy;
          });
        });
      api
        .decisions(ticker)
        .then((d) => {
          if (!alive) return;
          const last = d.decisions.length ? d.decisions[d.decisions.length - 1] : null;
          setRows((r) => {
            const copy = r.slice();
            copy[idx] = { ...copy[idx], lastDecision: last };
            return copy;
          });
        })
        .catch(() => {});
    });
    // One batched trigger scan hits every ticker once rather than N
    // separate calls; cheaper on the yfinance.calendar path.
    api
      .triggersAll()
      .then((r) => {
        if (!alive) return;
        setRows((rows) => {
          const byTicker = new Map(r.tickers.map((t) => [t.ticker, t]));
          return rows.map((row) => {
            const t = byTicker.get(row.ticker);
            return t ? { ...row, triggers: t.triggers } : row;
          });
        });
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="p-4">
      <Panel
        title="Watchlist"
        right={<Chip tone="data">{rows.length} tickers · Phase 0 grid</Chip>}
      >
        <div className="divide-y divide-hairline">
          <div className="grid grid-cols-[80px_120px_90px_90px_90px_110px_1fr_90px] items-center gap-2 border-b border-hairline px-3 py-2 label-micro text-faint">
            <span>Ticker</span>
            <span className="text-right">Last</span>
            <span className="text-right">Chg %</span>
            <span className="text-right">RSI14</span>
            <span className="text-right">Vol 20d</span>
            <span className="text-right">Triggers</span>
            <span className="pl-2">Last verdict</span>
            <span className="text-right">Open</span>
          </div>
          {rows.map((r) => (
            <WatchlistRow key={r.ticker} row={r} />
          ))}
        </div>
      </Panel>
      <div className="mt-3 text-[11px] text-faint">
        <span className="label-micro mr-2">tip</span>
        Click a ticker to open its research page. Phase B adds live event triggers so this
        dashboard refreshes when something a trader would react to actually happens.
      </div>
    </div>
  );
}

function WatchlistRow({ row }: { row: Row }) {
  const s = row.snapshot;
  const price = s?.price;
  const rsi = num(pick(s?.indicators, "momentum.rsi_14"));
  const vol20 = num(pick(s?.indicators, "volatility.vol_20d_ann_pct"));
  const changePct = price?.change_pct ?? null;
  const changeTone =
    changePct == null ? "neutral" : changePct > 0 ? "bull" : changePct < 0 ? "bear" : "neutral";
  const d = row.lastDecision;
  const verdictTone: "bull" | "bear" | "amber" | "neutral" =
    d?.action === "buy" ? "bull" : d?.action === "sell" ? "bear" : d?.gate_skipped ? "neutral" : "amber";

  const triggers = row.triggers;
  const nTriggers = triggers?.length ?? null;
  const triggersTone: "bull" | "bear" | "amber" | "neutral" =
    nTriggers == null
      ? "neutral"
      : nTriggers === 0
        ? "neutral"
        : triggers!.some((t) => t.direction === "bullish")
          ? "bull"
          : triggers!.some((t) => t.direction === "bearish")
            ? "bear"
            : "amber";

  return (
    <div className="grid grid-cols-[80px_120px_90px_90px_90px_110px_1fr_90px] items-center gap-2 px-3 py-2 text-[12px] hover:bg-panel-2/40">
      <Link to={`/t/${row.ticker}`} className="font-medium text-data hover:text-ink">
        {row.ticker}
      </Link>
      <span className="text-right text-ink tabular-nums">{fmt(price?.last)}</span>
      <span className="text-right tabular-nums">
        <Chip tone={changeTone}>{changePct != null ? pct(changePct) : "—"}</Chip>
      </span>
      <span className="text-right text-ink tabular-nums">{fmt(rsi, 1)}</span>
      <span className="text-right text-ink tabular-nums">{vol20 != null ? `${vol20.toFixed(1)}%` : "—"}</span>
      <span className="text-right">
        {nTriggers == null ? (
          <span className="text-faint">…</span>
        ) : nTriggers === 0 ? (
          <Chip tone="neutral">silent</Chip>
        ) : (
          <Chip tone={triggersTone}>
            {nTriggers} fired
          </Chip>
        )}
      </span>
      <span className="flex items-center gap-1 pl-2">
        {d ? (
          <>
            <Chip tone={verdictTone}>
              {d.as_of} · {d.action ?? (d.gate_skipped ? "skipped" : "—")}
            </Chip>
            <StageBadge stage={d.stage} />
          </>
        ) : (
          <span className="text-faint italic">no journal yet</span>
        )}
      </span>
      <span className="text-right">
        <Link
          to={`/t/${row.ticker}`}
          className="border border-hairline bg-panel-2 px-2 py-1 text-[10px] tracking-widest uppercase text-ink hover:border-data/60"
        >
          open
        </Link>
      </span>
    </div>
  );
}
