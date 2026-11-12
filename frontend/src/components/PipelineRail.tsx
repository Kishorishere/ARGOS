import type { NodeEvent } from "../lib/useRunStream";

type RailNode = {
  id: string;
  label: string;
  group: "gate" | "analyst" | "forecast" | "debate" | "judge" | "risk" | "decision" | "short";
};

/* Exact node IDs as registered in argos/graph.py, in execution order. */
const N = {
  data: { id: "data", label: "data", group: "gate" },
  exit: { id: "exit_manager", label: "exit", group: "gate" },
  exitShort: { id: "exit_short_circuit", label: "exit→end", group: "short" },
  router: { id: "analyst_router", label: "router", group: "gate" },
  fundamentals: { id: "fundamentals", label: "fund.", group: "analyst" },
  sentiment: { id: "sentiment", label: "sent.", group: "analyst" },
  technicals: { id: "technicals", label: "tech.", group: "analyst" },
  forecast: { id: "forecast", label: "forecast", group: "forecast" },
  bull: { id: "bull", label: "bull", group: "debate" },
  bear: { id: "bear", label: "bear", group: "debate" },
  judge: { id: "judge", label: "judge", group: "judge" },
  risk: { id: "risk_manager", label: "risk", group: "risk" },
  vetoShort: { id: "veto_short_circuit", label: "veto→end", group: "short" },
  decision: { id: "portfolio_manager", label: "decision", group: "decision" },
} as const satisfies Record<string, RailNode>;

const GROUP_TONE: Record<RailNode["group"], string> = {
  gate: "border-hairline-2 text-dim",
  analyst: "border-data/40 text-data",
  forecast: "border-data/50 text-data",
  debate: "border-amber/50 text-amber",
  judge: "border-amber/50 text-amber",
  risk: "border-bear/40 text-bear",
  decision: "border-bull/50 text-bull",
  short: "border-bear/30 text-bear/70",
};

interface RailProps {
  nodes: NodeEvent[];
  done: boolean;
}

export default function PipelineRail({ nodes, done }: RailProps) {
  /* hit counts per node → debate loop shows N rounds */
  const hits = new Map<string, number>();
  for (const ev of nodes) hits.set(ev.node, (hits.get(ev.node) ?? 0) + 1);
  const last = nodes[nodes.length - 1];
  const debateRounds = hits.get("bull") ?? 0;

  const box = (n: RailNode, row: number, col: number, colSpan = 2) => {
    const count = hits.get(n.id) ?? 0;
    const active = !done && last?.node === n.id;
    const fired = count > 0;
    const tone = fired ? GROUP_TONE[n.group] : "border-hairline text-faint";
    return (
      <div
        key={n.id}
        style={{ gridColumn: `${col} / span ${colSpan}`, gridRow: row }}
        className={`relative border px-2 py-1.5 text-center transition-all duration-300 ${tone} ${
          fired ? "bg-panel-2" : "bg-panel"
        } ${active ? "animate-pulse-node" : ""}`}
        title={`${n.id}${count > 1 ? ` ×${count}` : ""}`}
      >
        <div className="text-[10px] tracking-wider uppercase">{n.label}</div>
        <div className="text-[9px] text-faint tabular-nums">
          {fired ? (count > 1 ? `×${count}` : "✓") : "·"}
        </div>
        {active && <span className="absolute -top-1 -right-1 h-1.5 w-1.5 bg-bull" />}
      </div>
    );
  };

  return (
    <div className="panel overflow-x-auto p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="label-micro">pipeline · graph.stream(updates)</span>
        <span className="text-[10px] text-faint tabular-nums">
          {nodes.length} events{debateRounds ? ` · debate loop ×${debateRounds}` : ""}
          {done ? " · complete" : ""}
        </span>
      </div>

      {/* head: data → exit gate → router → analysts(∥) → forecast → bull⇄bear → judge */}
      <div className="grid min-w-[760px] grid-cols-14 items-center gap-1.5">
        {box(N.data, 1, 1)}
        {box(N.exit, 1, 3)}
        {box(N.router, 1, 5)}
        {box(N.fundamentals, 2, 5)}
        {box(N.sentiment, 2, 7)}
        {box(N.technicals, 2, 9)}
        {box(N.forecast, 1, 7)}
        {box(N.bull, 1, 9)}
        {box(N.bear, 1, 11)}
        {box(N.judge, 1, 13)}

        {/* debate loop arc: bear → bull */}
        <div style={{ gridColumn: "9 / span 4", gridRow: 3 }} className="relative h-5">
          <svg className="absolute inset-0 h-full w-full" preserveAspectRatio="none">
            <defs>
              <marker id="rail-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="var(--color-amber)" />
              </marker>
            </defs>
            <path
              d="M 80 0 C 80 14, 10 14, 10 2"
              fill="none"
              stroke="var(--color-amber)"
              strokeWidth="1"
              opacity={hits.get("bear") ? 0.9 : 0.25}
              markerEnd="url(#rail-arrow)"
            />
          </svg>
          <span className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-panel px-1 text-[9px] text-amber">
            bull_again
          </span>
        </div>
      </div>

      {/* tail: exit/veto short-circuits, risk gate → decision */}
      <div className="mt-2 grid min-w-[760px] grid-cols-14 items-center gap-1.5">
        {box(N.exitShort, 1, 3, 3)}
        {box(N.risk, 1, 7, 3)}
        {box(N.vetoShort, 1, 11)}
        {box(N.decision, 1, 13)}
        <div style={{ gridColumn: "1 / -1", gridRow: 2 }} className="text-center">
          <span className="text-[9px] text-faint">exit_short_circuit / veto_short_circuit → END</span>
        </div>
      </div>
    </div>
  );
}
