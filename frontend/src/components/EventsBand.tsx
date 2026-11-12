/* Events band — live triggers for one ticker. Rendered between the chart
   and the four analyst panels on the Ticker page.

   Design: silent when nothing fires (per the "80% oracle" pattern in
   scripts/event_alerts.py). When a trigger does fire, every column needs
   to fit in one row with enough detail that a trader doesn't have to
   expand it: direction + kind + magnitude + the exact rule.

   The Edge Gate does NOT yet read these triggers (that A/B lives in
   Phase D). This band is observational — a lens, not a controller. */

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Trigger } from "../types/snapshot";
import { Chip, Empty, Panel } from "./ui";

export default function EventsBand({ ticker }: { ticker: string }) {
  const [triggers, setTriggers] = useState<Trigger[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setTriggers(null);
    setErr(null);
    api
      .triggersFor(ticker)
      .then((r) => alive && setTriggers(r.triggers))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [ticker]);

  return (
    <Panel
      title={`${ticker} · events`}
      right={
        <div className="flex items-center gap-2">
          <Chip tone="data">Phase B · observational</Chip>
          {triggers && <Chip tone={triggers.length ? "amber" : "neutral"}>{triggers.length} fired</Chip>}
        </div>
      }
    >
      <div className="px-3 py-3">
        {err && <div className="text-[11px] text-bear">{err}</div>}
        {!triggers && !err && <Empty>scanning triggers…</Empty>}
        {triggers && triggers.length === 0 && (
          <Empty>no triggers fired for this ticker today — the loop would stay silent.</Empty>
        )}
        {triggers && triggers.length > 0 && (
          <ul className="space-y-1.5">
            {triggers.map((t, i) => (
              <li
                key={`${t.kind}-${i}`}
                className="flex items-center gap-2 border border-hairline bg-panel-2/60 px-2 py-1.5 text-[12px]"
              >
                <TriggerDirectionChip t={t} />
                <span className="text-dim uppercase tracking-widest text-[10px]">{t.source}</span>
                <span className="text-ink">{t.kind}</span>
                <span className="text-faint">·</span>
                <span className="text-dim">{t.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

function TriggerDirectionChip({ t }: { t: Trigger }) {
  const tone: "bull" | "bear" | "amber" | "neutral" =
    t.direction === "bullish" ? "bull" : t.direction === "bearish" ? "bear" : "amber";
  return <Chip tone={tone}>{t.direction}</Chip>;
}
