/* Stage badge — which trust tier a journalled decision belongs to.
   Every live verdict rendered in the UI carries this chip so a reader
   never confuses a historical backtest replay with a live committee call.

   Taxonomy (also documented in server/routers/decisions.py::_stage_for):
     backtest      — historical replay, lowest trust
     walk_forward  — in-sample tuned → out-of-sample scored
     paper         — live data, no money moving
     live          — real money (not shipped in this project; reserved) */

import type { Stage } from "../types/snapshot";
import { Chip } from "./ui";

const LABEL: Record<Stage, string> = {
  backtest: "backtest",
  walk_forward: "walk-fwd",
  paper: "paper",
  live: "live",
};

const TONE: Record<Stage, "bull" | "bear" | "amber" | "data" | "neutral"> = {
  backtest: "neutral",
  walk_forward: "data",
  paper: "amber",
  live: "bull",
};

export default function StageBadge({ stage }: { stage: Stage }) {
  return <Chip tone={TONE[stage]}>{LABEL[stage]}</Chip>;
}
