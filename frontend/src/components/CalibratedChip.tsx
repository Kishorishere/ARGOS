/* Shows the committee's raw confidence next to the historical empirical
   accuracy of that confidence bucket on the reference run. A persistent
   gap between raw and calibrated is a research finding on its own —
   "the system says 0.8 but historically has been right 55% of the time
   at this confidence." */

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { CalibrationResponse } from "../types/snapshot";
import { Chip } from "./ui";

export default function CalibratedChip({
  rawConfidence,
  horizon = 5,
}: {
  rawConfidence: number | null | undefined;
  horizon?: number;
}) {
  const [curve, setCurve] = useState<CalibrationResponse | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .calibration(horizon)
      .then((c) => alive && setCurve(c))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [horizon]);

  if (rawConfidence == null || !Number.isFinite(rawConfidence)) {
    return <Chip tone="neutral">no conf</Chip>;
  }

  const rawPct = Math.round(rawConfidence * 100);
  const bin = curve?.bins.find((b) => {
    if (b.confidence_bin_hi >= 1.0 && rawConfidence === 1.0) return true;
    return rawConfidence >= b.confidence_bin_lo && rawConfidence < b.confidence_bin_hi;
  });
  const cal = bin?.empirical_accuracy;
  const calPct = cal != null ? Math.round(cal * 100) : null;
  const gap = calPct != null ? rawPct - calPct : null;
  const tone: "bull" | "bear" | "amber" | "neutral" =
    gap == null ? "neutral" : Math.abs(gap) <= 5 ? "bull" : Math.abs(gap) <= 15 ? "amber" : "bear";

  return (
    <span className="flex items-center gap-1">
      <Chip tone="neutral">raw {rawPct}%</Chip>
      {calPct != null ? (
        <Chip tone={tone}>cal {calPct}% · h={horizon} · n={bin?.n ?? 0}</Chip>
      ) : (
        <Chip tone="neutral">cal — (bucket n&lt;3)</Chip>
      )}
    </span>
  );
}
