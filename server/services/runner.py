"""Live pipeline runner — streams one graph run to the SSE hub.

The Argos core is imported unchanged: compile_graph() → graph.stream(state,
stream_mode="updates") yields one {node_name: partial_state} dict per
completed node. Each becomes an SSE `node` event, so the UI replays the
graph exactly as it executed (including bull⇄bear debate loops).

On completion the final state is turned into a journal record with the same
build_record()/append_record() code path as run_backtest.py.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from argos.journal import append_record, build_record  # noqa: E402
from server.services.stream_hub import hub  # noqa: E402

DECISIONS = REPO_ROOT / "logs" / "decisions"
NASDAQ_DIR = DECISIONS / "nasdaq_focus"
LIVE_DIR = DECISIONS / "live"
WF_DIR = DECISIONS / "wf"
PAPER_DIR = DECISIONS / "paper"

_graph = None
_graph_lock = threading.Lock()


def _get_graph():
    """Compile the LangGraph pipeline once, lazily (import cost off the API)."""
    global _graph
    with _graph_lock:
        if _graph is None:
            from argos.graph import compile_graph

            _graph = compile_graph()
        return _graph


def _load_window(market: str, ticker: str, date: str, live: bool = False) -> dict:
    if market == "nepse":
        from argos.data.nepse_ohlcv import load_nepse_ohlcv

        return load_nepse_ohlcv(ticker, date)
    from argos.data.ohlcv_loader import load_ohlcv

    return load_ohlcv(ticker, date, allow_today=live)


def _ohlcv_summary(ohlcv_data: dict) -> dict:
    records = ohlcv_data.get("ohlcv", [])
    if not records:
        return {"rows": 0, "date_range": "N/A", "last_5_closes": []}
    last_5 = records[-5:] if len(records) >= 5 else records
    return {
        "rows": ohlcv_data.get("rows", len(records)),
        "date_range": ohlcv_data.get("date_range", "N/A"),
        "last_5_closes": [r["close"] for r in last_5],
        "last_close": records[-1]["close"],
        "last_date": records[-1]["date"],
    }


def _journal_dir(market: str, ticker: str, live: bool = False, stage: str = "backtest") -> Path:
    """Where to write this run's journal.

    Stage taxonomy (matches server/routers/decisions.py::_stage_for):
      backtest     → logs/decisions/nasdaq_focus/{TICKER}/   (paper reference run)
      backtest     → logs/decisions/                         (legacy flat)
      live         → logs/decisions/live/{market}/{TICKER}/
      walk_forward → logs/decisions/wf/{market}/{TICKER}/
      paper        → logs/decisions/paper/{market}/{TICKER}/

    Different stages never mix. The paper's reference backtest under
    `nasdaq_focus/` stays append-only and clean; stage-badged namespaces
    live under their own roots so the Analytics view can tell a reader
    which trust tier each row belongs to.

    `live=True` is a back-compat alias for `stage="live"`.
    """
    if live or stage == "live":
        return LIVE_DIR / market / ticker.upper()
    if stage == "walk_forward":
        return WF_DIR / market / ticker.upper()
    if stage == "paper":
        return PAPER_DIR / market / ticker.upper()
    return (NASDAQ_DIR / ticker.upper()) if market == "nasdaq" else DECISIONS


def _round_of(state: dict, delta: Any) -> int | None:
    """Debate round for a node event (None outside the bull/bear loop)."""
    if isinstance(delta, dict):
        entries = delta.get("debate_transcript")
        if isinstance(entries, list) and entries and isinstance(entries[-1], dict):
            r = entries[-1].get("round")
            if isinstance(r, int):
                return r
    return None


def _run(params: dict, run_id: str) -> None:
    from argos.graph import create_initial_state

    st = hub.get(run_id)
    if st is None:  # pragma: no cover — defensive
        return
    st.status = "running"
    market = params["market"]
    ticker = params["ticker"].upper()
    date = params["date"]
    persist = bool(params.get("persist", False))
    live = bool(params.get("live", False))
    stage = str(params.get("stage") or ("live" if live else "backtest"))
    t0 = time.time()

    try:
        st.push("started", {"run_id": run_id, "params": params})

        window = _load_window(market, ticker, date, live=live or stage in ("live", "paper"))
        st.push("node", {"node": "data", "delta": _ohlcv_summary(window), "round": None})

        state = create_initial_state(
            ticker=ticker,
            as_of_date=date,
            ohlcv_window=window,
        )
        rounds = params.get("max_debate_rounds")
        if isinstance(rounds, int) and rounds > 0:
            state["max_debate_rounds"] = rounds
        graph = _get_graph()

        final_state: dict[str, Any] = dict(state)
        for update in graph.stream(state, stream_mode="updates"):
            # langgraph yields {node_name: partial_state} (or batched lists)
            if isinstance(update, list):
                items = [it for u in update for it in (u.items() if isinstance(u, dict) else [])]
            elif isinstance(update, dict):
                items = list(update.items())
            else:
                continue
            for node, delta in items:
                if node in ("__end__", "__start__", "__interrupt__"):
                    continue
                if isinstance(delta, dict):
                    final_state.update(delta)
                st.push(
                    "node",
                    {
                        "node": node,
                        "delta": _safe_delta(node, delta),
                        "round": _round_of(final_state, delta) if node in ("bull", "bear") else None,
                    },
                )

        duration = round(time.time() - t0, 3)
        record = build_record(
            ticker, date, final_state, _ohlcv_summary(window), duration
        )
        journal_path = None
        if persist:
            path = append_record(record, _journal_dir(market, ticker, live=live, stage=stage), ticker, date)
            journal_path = str(path.relative_to(REPO_ROOT))

        st.push(
            "done",
            {
                "run_id": run_id,
                "duration_s": duration,
                "record": record,
                "journal_path": journal_path,
                "persisted": persist,
            },
        )
        st.finish("done")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        st.push("error", {"message": str(e), "traceback": traceback.format_exc(limit=5)})
        st.finish("error")


def _safe_delta(node: str, delta: Any) -> Any:
    """Strip huge blobs (prompt audits, indicator snapshots) from live events."""
    if not isinstance(delta, dict):
        return delta if isinstance(delta, (str, int, float, bool, type(None))) else None
    out = dict(delta)
    out.pop("analyst_prompts", None)
    # keep indicator_snapshot out of the firehose — the full record has it
    sig = out.get("technicals_signal")
    if isinstance(sig, dict):
        sig = dict(sig)
        sig.pop("indicator_snapshot", None)
        out["technicals_signal"] = sig
    return out


def start_run(params: dict) -> str:
    """Create the run in the hub and launch its worker thread."""
    import uuid

    run_id = str(uuid.uuid4())
    hub.create(run_id, params)
    threading.Thread(target=_run, args=(params, run_id), daemon=True).start()
    return run_id
