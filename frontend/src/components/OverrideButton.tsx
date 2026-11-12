/* "I disagree" — opens a tiny modal that captures a user override of the
   committee's final call. One POST to /api/overrides; the user's reason is
   mandatory because the log is only useful if every row has one.

   Tracked over time, this becomes a research artifact: user vs committee
   win rate per ticker, per confidence bucket, per regime. */

import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { JournalRecord } from "../types/record";

interface Props {
  ticker: string;
  asOf: string | null;
  record: JournalRecord | null;
  runId?: string | null;
  journalPath?: string | null;
}

export default function OverrideButton({ ticker, asOf, record, runId, journalPath }: Props) {
  const [open, setOpen] = useState(false);
  const [userAction, setUserAction] = useState<string>("buy");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  useEffect(() => {
    if (!open) {
      setReason("");
      setErr(null);
      setOk(false);
      setUserAction("buy");
    }
  }, [open]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const committeeAction = record?.final_decision?.action ?? null;
  const committeeConfidence = record?.final_decision?.confidence ?? null;

  const submit = useCallback(async () => {
    if (!asOf || !reason.trim()) return;
    setSubmitting(true);
    setErr(null);
    try {
      await api.postOverride({
        ticker,
        as_of: asOf,
        committee_action: committeeAction,
        committee_confidence: committeeConfidence,
        user_action: userAction,
        user_reason: reason.trim(),
        run_id: runId ?? null,
        attempt_id: record?.attempt_id ?? null,
        journal_path: journalPath ?? null,
      });
      setOk(true);
      setTimeout(() => setOpen(false), 900);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }, [asOf, reason, ticker, committeeAction, committeeConfidence, userAction, runId, record, journalPath]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={!record || !asOf}
        className="border border-hairline bg-panel-2 px-2 py-1 text-[11px] tracking-widest uppercase text-dim hover:border-amber/60 hover:text-amber disabled:opacity-50"
        title="Log a disagreement with the committee verdict"
      >
        I disagree
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-void/70 backdrop-blur-[1px]"
            onClick={() => setOpen(false)}
            role="button"
            aria-label="close override dialog"
          />
          <div className="relative w-full max-w-md border border-hairline bg-panel p-4">
            <div className="label-micro mb-3 text-faint">override committee · {ticker} · {asOf}</div>
            <div className="mb-3 border border-hairline bg-void/40 px-2 py-1.5 text-[11px] text-dim">
              <div>
                <span className="text-faint">committee:</span>{" "}
                <span className="text-ink">{committeeAction ?? "—"}</span>
                {committeeConfidence != null && (
                  <span className="ml-1 text-faint">· {(committeeConfidence * 100).toFixed(0)}%</span>
                )}
              </div>
            </div>
            <div className="mb-3">
              <div className="label-micro mb-1 text-faint">your call</div>
              <div className="flex gap-1">
                {(["buy", "sell", "hold"] as const).map((a) => (
                  <button
                    key={a}
                    type="button"
                    onClick={() => setUserAction(a)}
                    className={`border px-2 py-1 text-[11px] tracking-widest uppercase transition-colors ${
                      userAction === a
                        ? "border-data/60 bg-data/10 text-ink"
                        : "border-hairline text-dim hover:border-hairline-2 hover:text-ink"
                    }`}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </div>
            <div className="mb-3">
              <div className="label-micro mb-1 text-faint">why — this is logged and scored later</div>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                className="w-full border border-hairline bg-void px-2 py-1.5 text-[12px] text-ink outline-none focus:border-data/60"
                placeholder="e.g. 'IV rank 85 but no event catalyst — I'd rather wait'"
              />
            </div>
            {err && <div className="mb-2 text-[11px] text-bear">{err}</div>}
            {ok && <div className="mb-2 text-[11px] text-bull">logged.</div>}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={submit}
                disabled={submitting || !reason.trim() || ok}
                className="border border-amber/60 bg-amber/10 px-3 py-1 text-[11px] tracking-widest uppercase text-amber hover:bg-amber/20 disabled:opacity-50"
              >
                {submitting ? "logging…" : ok ? "logged" : "log override"}
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="border border-hairline bg-panel-2 px-3 py-1 text-[11px] tracking-widest uppercase text-dim hover:text-ink"
              >
                cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
