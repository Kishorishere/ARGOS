import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { Snapshot, DecisionRow } from "../types/snapshot";
import type { JournalRecord } from "../types/record";
import { Chip, Empty } from "../components/ui";
import { fmt, pct } from "../lib/pick";
import Chart from "../components/Chart";
import CommitteeBand from "../components/CommitteeBand";
import EventsBand from "../components/EventsBand";
import FundamentalsPanel from "../components/panels/FundamentalsPanel";
import TechnicalsPanel from "../components/panels/TechnicalsPanel";
import SentimentPanel from "../components/panels/SentimentPanel";
import RiskPanel from "../components/panels/RiskPanel";
import OwnershipPanel from "../components/panels/OwnershipPanel";
import JournalDrawer from "../components/JournalDrawer";

type PanelFocus = "fundamentals" | "sentiment" | "technicals" | "risk";

export default function Ticker() {
  const { ticker: rawTicker } = useParams<{ ticker: string }>();
  const ticker = (rawTicker ?? "").toUpperCase();

  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [latest, setLatest] = useState<JournalRecord | null>(null);
  const [lastDecision, setLastDecision] = useState<DecisionRow | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [inspectFocus, setInspectFocus] = useState<PanelFocus | null>(null);

  useEffect(() => {
    if (!ticker) return;
    let alive = true;
    setSnapshot(null);
    setLatest(null);
    setLastDecision(null);
    setErr(null);

    api.snapshot(ticker).then((s) => alive && setSnapshot(s)).catch((e: Error) => alive && setErr(e.message));

    // Pull the latest decision so panels can light up with the last committee's
    // reasoning even before the user clicks Analyze. Phase A streams the LIVE
    // run into these same panels via SSE.
    api
      .decisions(ticker)
      .then(async (d) => {
        if (!alive) return;
        const last = d.decisions.length ? d.decisions[d.decisions.length - 1] : null;
        setLastDecision(last);
        if (!last) return;
        try {
          // Walk the journal path to find the market segment for the detail call.
          // journal_path shape: "logs/decisions/{market_or_namespace}/.../{TICKER}_{DATE}.jsonl"
          const parts = last.journal_path.split(/[\\/]/);
          const market =
            last.source === "live"
              ? parts[2] ?? "nasdaq"            // logs/decisions/live/{market}/...
              : parts[2] === "nasdaq_focus"
                ? "nasdaq"
                : "legacy";
          const detail = await api.journalDetail(market, ticker, last.as_of, "latest");
          if (alive && detail.attempts && detail.attempts[0]) setLatest(detail.attempts[0]);
        } catch {
          /* no detail — panels will just show the Idle state */
        }
      })
      .catch(() => {});

    return () => {
      alive = false;
    };
  }, [ticker]);

  if (!ticker) return <Empty>unknown ticker</Empty>;

  const price = snapshot?.price;
  const change = price?.change_pct ?? null;
  const tone = change == null ? "neutral" : change > 0 ? "bull" : change < 0 ? "bear" : "neutral";

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Top strip */}
      <div className="flex items-center gap-4 border-b border-hairline bg-panel px-4 py-2">
        <Link to="/" className="text-[11px] tracking-widest uppercase text-faint hover:text-ink">
          ← dashboard
        </Link>
        <div className="flex items-baseline gap-3">
          <span className="text-[18px] font-bold tracking-wider text-ink">{ticker}</span>
          <span className="text-ink tabular-nums text-[14px]">{fmt(price?.last)}</span>
          <Chip tone={tone}>{change != null ? pct(change) : "—"}</Chip>
          {snapshot?.live && <Chip tone="data">LIVE</Chip>}
        </div>
        <span className="ml-auto text-[10px] uppercase tracking-widest text-faint">
          {snapshot?.freshness?.collected_at
            ? `snapshot ${snapshot.freshness.collected_at.replace("T", " ").replace("+00:00", "Z")}`
            : "loading…"}
        </span>
        {err && <span className="text-[10px] text-bear">{err}</span>}
      </div>

      {/* Band 1 — chart */}
      <div className="p-4">
        <Chart ticker={ticker} />
      </div>

      {/* Band 1.5 — events. Silent when nothing's firing. */}
      <div className="px-4 pb-3">
        <EventsBand ticker={ticker} />
      </div>

      {/* Band 2 — five two-layer panels. Each panel pairs raw numbers with
          the structured verdict of the agent that already reasoned over
          them; the prompt ↗ button opens that agent's exact prompt +
          structured output in the shared journal drawer. */}
      <div className="grid grid-cols-1 gap-3 px-4 pb-4 md:grid-cols-2 xl:grid-cols-5">
        <FundamentalsPanel
          snapshot={snapshot}
          latestJournal={latest}
          onInspect={() => setInspectFocus("fundamentals")}
        />
        <TechnicalsPanel
          snapshot={snapshot}
          latestJournal={latest}
          onInspect={() => setInspectFocus("technicals")}
        />
        <SentimentPanel
          ticker={ticker}
          snapshot={snapshot}
          latestJournal={latest}
          onInspect={() => setInspectFocus("sentiment")}
        />
        <OwnershipPanel
          ticker={ticker}
          latestJournal={latest}
          onInspect={() => setInspectFocus("fundamentals")}
        />
        <RiskPanel
          snapshot={snapshot}
          latestJournal={latest}
          onInspect={() => setInspectFocus("risk")}
        />
      </div>

      {inspectFocus && latest && (
        <JournalDrawer
          ticker={ticker}
          record={latest}
          focus={inspectFocus}
          onClose={() => setInspectFocus(null)}
        />
      )}

      {/* Band 3 — committee */}
      <div className="px-4 pb-6">
        <CommitteeBand ticker={ticker} />
        {lastDecision && (
          <div className="mt-2 text-[11px] text-faint">
            <span className="label-micro mr-2">last journalled decision</span>
            {lastDecision.as_of} · {lastDecision.action ?? "—"} · source={lastDecision.source}
            {latest?.final_decision?.rationale && (
              <span className="ml-3 italic">"{String(latest.final_decision.rationale).slice(0, 180)}…"</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
