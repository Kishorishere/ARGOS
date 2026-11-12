"""In-memory run registry + SSE fan-out.

Each live run gets a RunState holding an ordered event log. SSE subscribers
replay the full history on connect (so page refreshes resume cleanly) and
then block on a Condition until new events arrive or the run finishes.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class RunState:
    run_id: str
    params: dict
    status: str = "queued"  # queued | running | done | error
    started_at: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)  # {seq, event, data}
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)

    def push(self, event: str, data: Any) -> None:
        with self._cond:
            self.events.append({"seq": len(self.events), "event": event, "data": data})
            self._cond.notify_all()

    def finish(self, status: str) -> None:
        with self._cond:
            self.status = status
            self._cond.notify_all()


class RunHub:
    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}
        self._lock = threading.Lock()

    def create(self, run_id: str, params: dict) -> RunState:
        st = RunState(run_id=run_id, params=params)
        with self._lock:
            self._runs[run_id] = st
        return st

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def list(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "run_id": r.run_id,
                    "params": r.params,
                    "status": r.status,
                    "started_at": r.started_at,
                    "n_events": len(r.events),
                }
                for r in sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)
            ]

    def sse_events(self, run_id: str) -> Iterator[dict]:
        """Yield events for a run — replay history, then live until done."""
        st = self.get(run_id)
        if st is None:
            yield {"seq": 0, "event": "error", "data": {"message": f"unknown run {run_id}"}}
            return
        idx = 0
        while True:
            with st._cond:
                if idx >= len(st.events) and st.status in ("queued", "running"):
                    st._cond.wait(timeout=15)  # heartbeat gap for idle runs
                new = st.events[idx:]
                idx = len(st.events)
                finished = st.status in ("done", "error")
            for ev in new:
                yield ev
            if finished and idx >= len(st.events):
                return
            if not new:
                yield {"seq": -1, "event": "ping", "data": {}}


hub = RunHub()
