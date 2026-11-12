"""Argos Terminal — FastAPI backend.

Wraps the existing Argos LangGraph pipeline (argos/) behind HTTP + SSE:

  GET  /api/system                — cache / Kronos / universe / market status
  GET  /api/journals?market=...   — decision-journal tree (read-only)
  GET  /api/journals/{m}/{t}/{d}  — full JSONL attempts for one (ticker, date)
  POST /api/runs                  — fire a live pipeline run
  GET  /api/runs/{id}/stream      — SSE stream of node-by-node updates
  GET  /api/reports/summary       — eval summary.json (if present)

The pipeline itself is imported unchanged from argos/graph.py; live runs use
graph.stream(stream_mode="updates") so every node completion becomes an SSE
event, and the final record is built with argos.journal.build_record — the
exact same code path as the CLI backtest runners.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env", override=True)

from server.routers import (  # noqa: E402
    analytics,
    analyze,
    calibration,
    cost,
    decisions,
    journals,
    news,
    ohlcv,
    overrides,
    ownership,
    reports,
    runs,
    snapshot,
    system,
    triggers,
)

app = FastAPI(title="Argos Terminal", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4444",
        "http://127.0.0.1:4444",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "argos-terminal"}


app.include_router(system.router)
app.include_router(journals.router)
app.include_router(runs.router)
app.include_router(reports.router)
# ── Phase 0 plumbing ────────────────────────────────────────────────────────
app.include_router(snapshot.router)   # GET /api/snapshot/{ticker}
app.include_router(ohlcv.router)      # GET /api/ohlcv/{ticker}
app.include_router(decisions.router)  # GET /api/decisions/{ticker}
app.include_router(analyze.router)    # POST /api/analyze/{ticker}
# ── Phase B event-driven firing ─────────────────────────────────────────────
app.include_router(triggers.router)   # GET /api/triggers, /api/triggers/{ticker}
# ── Phase E trust features ──────────────────────────────────────────────────
app.include_router(overrides.router)     # POST /api/overrides, GET /api/overrides
app.include_router(calibration.router)   # GET /api/calibration
app.include_router(cost.router)          # GET /api/cost/today
app.include_router(analytics.router)     # GET /api/analytics/abstention
# ── Phase C ownership + news ────────────────────────────────────────────────
app.include_router(ownership.router)     # GET /api/ownership/{ticker}
app.include_router(news.router)          # GET /api/news/{ticker}

# Serve the built frontend (npm run build) — added last so /api routes win.
# SPA fallback: unknown non-/api GETs return index.html so deep links
# (/journal/nasdaq/NVDA/2024-01-02, /analytics) work on refresh.
_dist = REPO_ROOT / "frontend" / "dist"
if _dist.exists():

    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404 or scope.get("method") != "GET":
                    raise
                if path.replace("\\", "/").lstrip("/").startswith("api/"):
                    return JSONResponse({"detail": f"not found: /{path}"}, status_code=404)
                return await super().get_response("index.html", scope)

    app.mount("/", SPAStaticFiles(directory=str(_dist), html=True), name="frontend")
else:

    @app.get("/")
    def root() -> dict:
        return {
            "service": "argos-terminal",
            "hint": "frontend not built — run `npm run build` in frontend/ "
                    "or use the Vite dev server on :5173",
        }
