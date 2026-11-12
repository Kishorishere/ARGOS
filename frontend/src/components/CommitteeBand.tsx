/* Live committee band — calls POST /api/analyze/{ticker}, then subscribes
   to /api/runs/{id}/stream and renders the pipeline as it executes.
   When the run completes, the full RecordView drops in below.

   Analyze is the only user-triggered LLM call on the per-ticker page.
   Everything else (snapshot, decisions, chart) is free static data — this
   is where the tokens go. Phase E will add a running cost meter here. */

import { useCallback, useState } from "react";
import { api } from "../lib/api";
import { useRunStream } from "../lib/useRunStream";
import PipelineRail from "./PipelineRail";
import RecordView from "./record/RecordView";
import OverrideButton from "./OverrideButton";
import CalibratedChip from "./CalibratedChip";
import { Chip, Empty, Panel } from "./ui";

export default function CommitteeBand({ ticker }: { ticker: string }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchErr, setLaunchErr] = useState<string | null>(null);
  const { state } = useRunStream(runId);

  const launch = useCallback(async () => {
    setLaunching(true);
    setLaunchErr(null);
    try {
      const r = await api.analyze(ticker, { market: "nasdaq" });
      setRunId(r.run_id);
    } catch (e) {
      setLaunchErr((e as Error).message);
    } finally {
      setLaunching(false);
    }
  }, [ticker]);

  const running = state.status === "running" || state.status === "connecting";
  const done = state.status === "done";

  return (
    <Panel
      title={`${ticker} · committee`}
      right={
        <div className="flex items-center gap-2">
          {done && state.persisted && (
            <Chip tone="neutral">journalled</Chip>
          )}
          {running && <Chip tone="amber">running</Chip>}
          {state.status === "error" && <Chip tone="bear">error</Chip>}
          <button
            type="button"
            onClick={launch}
            disabled={launching || running}
            className="border border-hairline bg-panel-2 px-3 py-1 text-[11px] tracking-widest uppercase text-ink hover:border-data/60 disabled:opacity-50"
          >
            {launching ? "launching…" : running ? "analyzing…" : done ? "re-analyze" : "Analyze"}
          </button>
        </div>
      }
    >
      <div className="space-y-3 px-3 py-3">
        {launchErr && <div className="text-[11px] text-bear">{launchErr}</div>}
        {!runId && !launchErr && (
          <Empty>
            no committee run yet for this ticker. click Analyze to call the agents on today's data.
          </Empty>
        )}
        {runId && (
          <>
            <PipelineRail nodes={state.nodes} done={done} />
            {state.error && <div className="text-[11px] text-bear">{state.error}</div>}
            {done && state.record && (
              <>
                <div className="flex flex-wrap items-center gap-2 border border-hairline bg-void/40 px-2 py-1.5">
                  <span className="label-micro text-faint">final</span>
                  <Chip tone="data">{state.record.final_decision?.action ?? "—"}</Chip>
                  <CalibratedChip rawConfidence={state.record.final_decision?.confidence ?? null} />
                  <span className="ml-auto">
                    <OverrideButton
                      ticker={ticker}
                      asOf={state.record.date}
                      record={state.record}
                      runId={state.record.run_id ?? runId}
                      journalPath={state.journalPath}
                    />
                  </span>
                </div>
                <RecordView record={state.record} showJson={false} />
              </>
            )}
            {running && !state.record && (
              <div className="text-[11px] text-faint italic">
                agents working… {state.nodes.length} nodes completed so far.
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
