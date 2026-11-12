/* Today's cumulative live-committee cost, rendered as a small chip in
   the app header. Polls every 60s so the user sees the meter tick as
   they fire Analyze buttons, without being a bandwidth hog. */

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { CostTodayResponse } from "../types/snapshot";
import { Chip } from "./ui";

const POLL_MS = 60_000;

export default function CostChip() {
  const [cost, setCost] = useState<CostTodayResponse | null>(null);

  useEffect(() => {
    let alive = true;
    const pull = () => api.costToday().then((c) => alive && setCost(c)).catch(() => {});
    pull();
    const h = window.setInterval(pull, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(h);
    };
  }, []);

  if (!cost) return <Chip tone="neutral">cost today: …</Chip>;
  const tone: "bull" | "amber" | "bear" | "neutral" =
    cost.usd_estimate < 0.5 ? "neutral" : cost.usd_estimate < 2 ? "amber" : "bear";
  return (
    <Chip tone={tone}>
      today ${cost.usd_estimate.toFixed(2)} · {cost.total_tokens.toLocaleString()} tok · {cost.n_records} runs
    </Chip>
  );
}
