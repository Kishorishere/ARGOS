/* Slide-in right drawer used by two call sites:
     1. a decision dot click on the chart → shows the full RecordView
        for that (ticker, date) attempt
     2. the "view prompt + call" button inside any two-layer panel →
        same drawer, scoped to the agent's prompt + structured output
*/

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { marketForDecision } from "../lib/journal";
import type { DecisionRow } from "../types/snapshot";
import type { JournalRecord } from "../types/record";
import RecordView from "./record/RecordView";
import StageBadge from "./StageBadge";
import { Chip, Empty } from "./ui";

interface Props {
  ticker: string;
  decision?: DecisionRow | null;
  /* Optional preloaded record — used by the prompt-audit path so the
     drawer doesn't need a second fetch when the parent already has it. */
  record?: JournalRecord | null;
  focus?: "all" | "fundamentals" | "sentiment" | "technicals" | "judge" | "risk";
  onClose: () => void;
}

export default function JournalDrawer({ ticker, decision, record, focus = "all", onClose }: Props) {
  const [loaded, setLoaded] = useState<JournalRecord | null>(record ?? null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (record) {
      setLoaded(record);
      return;
    }
    if (!decision) return;
    let alive = true;
    setLoaded(null);
    setErr(null);
    const market = marketForDecision(decision);
    api
      .journalDetail(market, ticker, decision.as_of, "latest")
      .then((d) => alive && setLoaded(d.attempts?.[0] ?? null))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [ticker, decision, record]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40">
      <div
        className="absolute inset-0 bg-void/70 backdrop-blur-[1px]"
        onClick={onClose}
        role="button"
        aria-label="close drawer"
      />
      <aside className="absolute top-0 right-0 flex h-full w-full max-w-[720px] flex-col border-l border-hairline bg-panel shadow-2xl">
        <header className="flex items-center gap-3 border-b border-hairline px-4 py-2">
          <span className="text-[13px] font-bold tracking-wider text-ink">{ticker}</span>
          {decision && (
            <>
              <span className="text-data">{decision.as_of}</span>
              <StageBadge stage={decision.stage} />
              {typeof decision.n_triggers === "number" && decision.n_triggers > 0 && (
                <Chip tone="amber">{decision.n_triggers} triggers</Chip>
              )}
            </>
          )}
          {focus !== "all" && <Chip tone="data">focus: {focus}</Chip>}
          <button
            type="button"
            onClick={onClose}
            className="ml-auto border border-hairline bg-panel-2 px-2 py-1 text-[10px] tracking-widest uppercase text-dim hover:text-ink"
          >
            close
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {err && <div className="text-[11px] text-bear">{err}</div>}
          {!loaded && !err && <Empty>loading journal…</Empty>}
          {loaded && focus === "all" && <RecordView record={loaded} />}
          {loaded && focus !== "all" && <FocusedAgent record={loaded} focus={focus} />}
        </div>
      </aside>
    </div>
  );
}

function FocusedAgent({
  record,
  focus,
}: {
  record: JournalRecord;
  focus: "fundamentals" | "sentiment" | "technicals" | "judge" | "risk";
}) {
  const signals = record.analyst_signals;
  const sig = signals && focus in signals ? signals[focus as keyof typeof signals] : null;
  const prompts = record.analyst_prompts ?? {};
  const prompt = prompts[focus];

  return (
    <div className="space-y-3">
      <div className="border border-hairline bg-void/40 px-3 py-2 text-[11px] text-dim">
        <span className="label-micro mr-2 text-faint">focus</span>
        {focus}
      </div>
      {sig && (
        <div className="border border-hairline p-3">
          <div className="label-micro mb-1 text-faint">Structured output</div>
          <pre className="whitespace-pre-wrap text-[11px] text-ink">
            {JSON.stringify(sig, null, 2)}
          </pre>
        </div>
      )}
      {prompt?.system && (
        <div className="border border-hairline p-3">
          <div className="label-micro mb-1 text-faint">System prompt</div>
          <pre className="whitespace-pre-wrap text-[11px] text-dim">{prompt.system}</pre>
        </div>
      )}
      {prompt?.user && (
        <div className="border border-hairline p-3">
          <div className="label-micro mb-1 text-faint">User prompt</div>
          <pre className="whitespace-pre-wrap text-[11px] text-dim">{prompt.user}</pre>
        </div>
      )}
      {focus === "judge" && record.judge_verdict && (
        <div className="border border-hairline p-3">
          <div className="label-micro mb-1 text-faint">Judge verdict</div>
          <pre className="whitespace-pre-wrap text-[11px] text-ink">
            {JSON.stringify(record.judge_verdict, null, 2)}
          </pre>
        </div>
      )}
      {focus === "risk" && record.risk_verdict && (
        <div className="border border-hairline p-3">
          <div className="label-micro mb-1 text-faint">Risk verdict</div>
          <pre className="whitespace-pre-wrap text-[11px] text-ink">
            {JSON.stringify(record.risk_verdict, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
