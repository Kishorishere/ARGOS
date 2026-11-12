/* Phase A.1 chart — real candles + MA(20/50/200) + RSI(14) sub-pane +
   clickable decision-dot markers wired to /api/decisions/{ticker}. */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
  type SeriesMarker,
  type Time,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import { api } from "../lib/api";
import type { DecisionRow, OhlcvBar } from "../types/snapshot";
import { Chip, Empty, Panel } from "./ui";
import { rsi, sma } from "../lib/indicators";
import { fmt } from "../lib/pick";
import JournalDrawer from "./JournalDrawer";

const COLORS = {
  up: "#22c55e",
  down: "#ef4444",
  ma20: "#60a5fa",
  ma50: "#f59e0b",
  ma200: "#a855f7",
  rsi: "#e5e7eb",
  grid: "#ffffff10",
  text: "#9ca3af",
  bg: "transparent",
} as const;

export default function Chart({ ticker }: { ticker: string }) {
  const [bars, setBars] = useState<OhlcvBar[] | null>(null);
  const [decisions, setDecisions] = useState<DecisionRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<DecisionRow | null>(null);

  useEffect(() => {
    let alive = true;
    setBars(null);
    setDecisions([]);
    setErr(null);
    setSelected(null);
    api
      .ohlcv(ticker, { lookbackDays: 365 })
      .then((o) => alive && setBars(o.ohlcv))
      .catch((e: Error) => alive && setErr(e.message));
    api
      .decisions(ticker)
      .then((d) => alive && setDecisions(d.decisions))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [ticker]);

  return (
    <Panel
      title={`${ticker} · chart`}
      right={
        <div className="flex items-center gap-2">
          <LegendDot color={COLORS.ma20} label="MA20" />
          <LegendDot color={COLORS.ma50} label="MA50" />
          <LegendDot color={COLORS.ma200} label="MA200" />
          {bars && <Chip tone="data">{bars.length} bars</Chip>}
          <Chip tone="neutral">{decisions.length} decisions</Chip>
        </div>
      }
    >
      <div className="relative">
        {err && <div className="px-3 py-2 text-[11px] text-bear">{err}</div>}
        {!bars && !err && (
          <div className="py-10">
            <Empty>loading candles…</Empty>
          </div>
        )}
        {bars && bars.length > 0 && (
          <ChartInner bars={bars} decisions={decisions} onSelect={setSelected} />
        )}
      </div>
      {selected && (
        <JournalDrawer
          ticker={ticker}
          decision={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </Panel>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1 text-[10px] uppercase tracking-widest text-faint">
      <span className="inline-block h-1.5 w-3" style={{ background: color }} />
      {label}
    </span>
  );
}

function ChartInner({
  bars,
  decisions,
  onSelect,
}: {
  bars: OhlcvBar[];
  decisions: DecisionRow[];
  onSelect: (d: DecisionRow) => void;
}) {
  const priceContainer = useRef<HTMLDivElement>(null);
  const rsiContainer = useRef<HTMLDivElement>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  const ma20 = useMemo(() => sma(bars, 20), [bars]);
  const ma50 = useMemo(() => sma(bars, 50), [bars]);
  const ma200 = useMemo(() => sma(bars, 200), [bars]);
  const rsi14 = useMemo(() => rsi(bars, 14), [bars]);

  // ── Chart lifecycle ───────────────────────────────────────────────────
  useEffect(() => {
    if (!priceContainer.current || !rsiContainer.current) return;

    const common = {
      autoSize: true,
      layout: { background: { color: COLORS.bg }, textColor: COLORS.text, fontSize: 11 },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderColor: COLORS.grid },
      timeScale: { borderColor: COLORS.grid, timeVisible: false, secondsVisible: false },
      crosshair: { mode: 1 as const },
    };

    const priceChart = createChart(priceContainer.current, common);
    const rsiChart = createChart(rsiContainer.current, {
      ...common,
      timeScale: { ...common.timeScale, visible: true },
    });
    priceChartRef.current = priceChart;
    rsiChartRef.current = rsiChart;

    const candle = priceChart.addSeries(CandlestickSeries, {
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
    });
    candleRef.current = candle;
    candle.setData(bars.map((b) => ({ time: b.date as Time, open: b.open, high: b.high, low: b.low, close: b.close })));

    const line = (color: string, data: typeof ma20) => {
      const s = priceChart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      s.setData(data.map((p) => ({ time: p.time as Time, value: p.value })));
      return s;
    };
    line(COLORS.ma20, ma20);
    line(COLORS.ma50, ma50);
    line(COLORS.ma200, ma200);

    const rsiSeries = rsiChart.addSeries(LineSeries, {
      color: COLORS.rsi,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    rsiSeries.setData(rsi14.map((p) => ({ time: p.time as Time, value: p.value })));
    rsiSeries.createPriceLine({
      price: 70,
      color: "#ef4444",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "70",
    });
    rsiSeries.createPriceLine({
      price: 30,
      color: "#22c55e",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "30",
    });

    // Sync time scales: panning/zooming one moves the other. v5 handlers
    // are referenced by identity for unsubscribe, so keep the fns around.
    let syncing = false;
    const onPriceRange = (range: { from: number; to: number } | null) => {
      if (syncing || !range) return;
      syncing = true;
      rsiChart.timeScale().setVisibleLogicalRange(range);
      syncing = false;
    };
    const onRsiRange = (range: { from: number; to: number } | null) => {
      if (syncing || !range) return;
      syncing = true;
      priceChart.timeScale().setVisibleLogicalRange(range);
      syncing = false;
    };
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(onPriceRange);
    rsiChart.timeScale().subscribeVisibleLogicalRangeChange(onRsiRange);

    // Markers for decisions on the candle series.
    const markers: SeriesMarker<Time>[] = decisions
      .filter((d) => d.as_of >= bars[0].date && d.as_of <= bars[bars.length - 1].date)
      .map((d) => {
        const action = d.action;
        const skipped = d.gate_skipped;
        const position = action === "sell" ? "aboveBar" : "belowBar";
        const shape = action === "buy" ? "arrowUp" : action === "sell" ? "arrowDown" : "circle";
        const color = skipped
          ? "#64748b"
          : action === "buy"
            ? COLORS.up
            : action === "sell"
              ? COLORS.down
              : "#f59e0b";
        const conf = d.confidence != null ? ` ${Math.round(d.confidence * 100)}%` : "";
        return {
          time: d.as_of as Time,
          position: position as "aboveBar" | "belowBar",
          color,
          shape: shape as "arrowUp" | "arrowDown" | "circle",
          text: skipped ? "skip" : (action ?? "?") + conf,
          size: 1,
        };
      });
    if (markers.length) createSeriesMarkers(candle, markers);

    // Click → find nearest decision by date, open drawer.
    const byDate = new Map(decisions.map((d) => [d.as_of, d]));
    const onChartClick = (param: { time?: Time | undefined }) => {
      const t = param.time;
      if (typeof t !== "string") return;
      let hit = byDate.get(t);
      if (!hit) {
        const base = new Date(t);
        for (let delta = 1; delta <= 3 && !hit; delta++) {
          for (const sign of [-1, 1]) {
            const d = new Date(base);
            d.setDate(base.getDate() + sign * delta);
            const key = d.toISOString().slice(0, 10);
            if (byDate.has(key)) {
              hit = byDate.get(key);
              break;
            }
          }
        }
      }
      if (hit) onSelect(hit);
    };
    priceChart.subscribeClick(onChartClick);

    priceChart.timeScale().fitContent();

    return () => {
      priceChart.timeScale().unsubscribeVisibleLogicalRangeChange(onPriceRange);
      rsiChart.timeScale().unsubscribeVisibleLogicalRangeChange(onRsiRange);
      priceChart.unsubscribeClick(onChartClick);
      priceChart.remove();
      rsiChart.remove();
      priceChartRef.current = null;
      rsiChartRef.current = null;
      candleRef.current = null;
    };
    // bars/decisions arrays change identity on ticker switch; rebuild the charts.
  }, [bars, decisions, ma20, ma50, ma200, rsi14, onSelect]);

  const last = bars[bars.length - 1];
  const prev = bars.length >= 2 ? bars[bars.length - 2] : null;
  const change = prev ? last.close - prev.close : 0;
  const changePct = prev && prev.close ? (change / prev.close) * 100 : 0;

  return (
    <div className="space-y-2 px-3 py-3">
      <div className="flex items-baseline gap-3">
        <span className="label-micro text-faint">last</span>
        <span className="text-ink tabular-nums text-[16px] font-medium">{fmt(last.close)}</span>
        <span className={changePct >= 0 ? "text-bull text-[12px] tabular-nums" : "text-bear text-[12px] tabular-nums"}>
          {changePct >= 0 ? "+" : ""}
          {changePct.toFixed(2)}%
        </span>
        <span className="text-faint text-[10px] uppercase tracking-widest">
          {bars[0].date} → {last.date}
        </span>
      </div>
      <div ref={priceContainer} style={{ height: 320, width: "100%" }} />
      <div className="flex items-center gap-2">
        <span className="label-micro text-faint">RSI 14</span>
      </div>
      <div ref={rsiContainer} style={{ height: 110, width: "100%" }} />
    </div>
  );
}
