import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { Snapshot, NewsResponse } from "../../types/snapshot";
import type { JournalRecord } from "../../types/record";
import { AgentIdle, PanelShell, SignalChip } from "./PanelShell";
import { Chip } from "../ui";

interface Props {
  ticker: string;
  snapshot: Snapshot | null;
  latestJournal?: JournalRecord | null;
  onInspect?: () => void;
}

/* Phase C upgrade: raw layer now shows real Finnhub news items when
   FINNHUB_API_KEY is set, otherwise falls back to the honest "no feed
   wired" message (matched by the server response's `error` field). */
export default function SentimentPanel({ ticker, latestJournal, onInspect }: Props) {
  const [news, setNews] = useState<NewsResponse | null>(null);

  useEffect(() => {
    let alive = true;
    setNews(null);
    api
      .news(ticker, { days: 7, limit: 8 })
      .then((n) => alive && setNews(n))
      .catch(() => {
        if (alive) setNews({ ticker, from: "", to: "", items: [], error: "fetch_failed" });
      });
    return () => {
      alive = false;
    };
  }, [ticker]);

  const raw =
    !news ? (
      <div className="text-[11px] text-faint italic">loading news…</div>
    ) : news.error ? (
      <div className="text-[11px] text-faint italic">{news.error}</div>
    ) : news.items.length === 0 ? (
      <div className="text-[11px] text-faint italic">no items in window.</div>
    ) : (
      <>
        <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-widest text-faint">
          <span>{news.items.length} items</span>
          <span>·</span>
          <span>{news.from} → {news.to}</span>
          {news.cached && <Chip tone="neutral">cached</Chip>}
        </div>
        <ul className="space-y-0.5">
          {news.items.slice(0, 6).map((it, i) => (
            <li key={`${it.datetime}-${i}`} className="truncate text-[11px]">
              <span className="text-faint tabular-nums mr-1 text-[10px]">
                {(it.datetime || "").slice(5, 10)}
              </span>
              <a
                href={it.url || "#"}
                target="_blank"
                rel="noreferrer"
                className="text-ink hover:text-data"
                title={it.headline}
              >
                {it.headline}
              </a>
              {it.category && (
                <span className="ml-1 text-faint text-[10px]">· {it.category.toLowerCase()}</span>
              )}
            </li>
          ))}
        </ul>
      </>
    );

  const agentSignal = latestJournal?.analyst_signals?.sentiment;
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
    <AgentIdle />
  );

  return (
    <PanelShell
      title="Sentiment"
      raw={raw}
      agent={agent}
      onInspect={onInspect}
      canInspect={!!agentSignal && !!onInspect}
    />
  );
}
