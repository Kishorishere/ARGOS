import { useMemo } from "react";
import type { Debate, JudgeVerdict, TranscriptEntry } from "../../types/record";
import { pct } from "../../lib/format";
import { Chip, ConfidenceBar, Empty, Panel } from "../ui";

function Bubble({ entry }: { entry: TranscriptEntry }) {
  const bull = entry.side === "bull";
  return (
    <div className={`flex ${bull ? "justify-start" : "justify-end"}`}>
      <div
        className={`max-w-[46%] border px-3 py-2 ${
          bull ? "border-bull/30 bg-bull/5" : "border-bear/30 bg-bear/5"
        }`}
      >
        <div className="mb-1 flex items-center gap-2">
          <span className={`text-[11px] font-bold tracking-[0.2em] uppercase ${bull ? "text-bull" : "text-bear"}`}>
            {bull ? "▲ bull" : "▼ bear"}
          </span>
          <Chip tone={entry.position === "buy" ? "bull" : "bear"}>{entry.position}</Chip>
          <span className="ml-auto text-[10px] text-dim tabular-nums">
            r{entry.round} · {pct(entry.confidence, 0)}
          </span>
        </div>
        <ConfidenceBar value={entry.confidence} />
        <p className="mt-2 text-[11px] leading-snug text-ink">
          <span className="text-dim">evidence: </span>
          {entry.strongest_evidence}
        </p>
        <p className="mt-1.5 text-[11px] leading-snug text-dim">
          <span className="text-faint">rebuttal: </span>
          {entry.rebuttal_of_opposing_view}
        </p>
      </div>
    </div>
  );
}

export function DebateTheater({
  debate,
  transcript,
}: {
  debate: Debate | null;
  transcript: TranscriptEntry[];
}) {
  const rounds = useMemo(() => {
    const map = new Map<number, TranscriptEntry[]>();
    for (const t of transcript) {
      const arr = map.get(t.round) ?? [];
      arr.push(t);
      map.set(t.round, arr);
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [transcript]);

  if (rounds.length === 0) {
    return (
      <Panel title="Debate Theater">
        <Empty>no debate transcript</Empty>
      </Panel>
    );
  }

  return (
    <Panel
      title="Debate Theater"
      right={
        <span className="text-[11px] text-dim tabular-nums">
          {debate?.rounds_completed ?? rounds.length} round{rounds.length > 1 ? "s" : ""} · bull{" "}
          <span className="text-bull">{pct(debate?.bull?.confidence, 0)}</span> · bear{" "}
          <span className="text-bear">{pct(debate?.bear?.confidence, 0)}</span>
        </span>
      }
    >
      <div className="space-y-4 p-3">
        {rounds.map(([n, entries]) => (
          <div key={n}>
            <div className="mb-2 flex items-center gap-3">
              <span className="text-[10px] tracking-[0.3em] text-faint uppercase">round {n}</span>
              <div className="h-px flex-1 bg-hairline" />
            </div>
            <div className="space-y-2">
              {entries.map((e, i) => (
                <Bubble key={i} entry={e} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

export function JudgeCard({ verdict }: { verdict: JudgeVerdict | null | undefined }) {
  if (!verdict) {
    return (
      <Panel title="Judge Verdict">
        <Empty>not recorded in this attempt (pre-judge_verdict schema)</Empty>
      </Panel>
    );
  }
  const side = verdict.winner_side;
  const tone = side === "bull" ? "text-bull" : side === "bear" ? "text-bear" : "text-amber";
  return (
    <Panel
      title="Judge Verdict"
      right={
        <span className={`text-[11px] tracking-widest uppercase ${tone}`}>
          {side === "tie" ? "tie" : `${side} wins`} · A={verdict.winner}
        </span>
      }
    >
      <div className="space-y-2 p-3">
        <ConfidenceBar value={verdict.confidence} />
        {verdict.key_factor && (
          <div className="border border-hairline bg-panel-2 px-2 py-1 text-[11px]">
            <span className="label-micro">key factor</span>
            <div className="text-ink">{verdict.key_factor}</div>
          </div>
        )}
        <p className="text-[11px] leading-relaxed text-dim whitespace-pre-wrap">{verdict.reasoning}</p>
      </div>
    </Panel>
  );
}
