"""SQLite-backed LLM response cache.

Purpose: the NASDAQ backtest hits ~750 (ticker, date) combos × ~8 LLM
calls each ≈ 6k API calls. First run costs money; re-runs (a bug fix,
a new eval metric, a re-scoring) should cost near zero. This cache
memoizes the LLM response keyed on the exact call payload.

Key = SHA-256 of (agent, provider/model, structured-output schema name,
prompt content). Provider is included in the key so a fallback to a
different provider on re-run gets its own cache slot, not the primary's.

Enable/disable with env: LLM_CACHE_ENABLED=1 (default on).
Path via env: LLM_CACHE_PATH (default: logs/llm_cache.sqlite).

Reproducibility: writes are atomic; concurrent backtest workers are safe
because SQLite serializes writes at the DB-file level.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_LOCK = threading.Lock()
_DB: Optional[sqlite3.Connection] = None


def is_enabled() -> bool:
    return os.environ.get("LLM_CACHE_ENABLED", "1") not in ("0", "false", "False", "")


def _db_path() -> Path:
    return Path(os.environ.get("LLM_CACHE_PATH", "logs/llm_cache.sqlite"))


def _connect() -> sqlite3.Connection:
    global _DB
    if _DB is not None:
        return _DB
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_cache (
            key TEXT PRIMARY KEY,
            agent TEXT NOT NULL,
            label TEXT NOT NULL,
            schema TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_llm_cache_agent ON llm_cache(agent)")
    conn.commit()
    _DB = conn
    return conn


def _stringify_messages(messages: Any) -> str:
    """Normalize whatever LangChain message shape the caller passed."""
    if isinstance(messages, str):
        return messages
    parts = []
    for m in messages:
        if isinstance(m, tuple) and len(m) == 2:
            parts.append(f"[{m[0]}]{m[1]}")
        elif hasattr(m, "content"):
            role = getattr(m, "type", getattr(m, "role", "?"))
            parts.append(f"[{role}]{m.content}")
        else:
            parts.append(str(m))
    return "\n\n".join(parts)


def _hash_key(cache_key: tuple) -> str:
    agent, label, messages, schema_name = cache_key
    h = hashlib.sha256()
    h.update(agent.encode("utf-8"))
    h.update(b"\x00")
    h.update(label.encode("utf-8"))
    h.update(b"\x00")
    h.update(schema_name.encode("utf-8"))
    h.update(b"\x00")
    h.update(_stringify_messages(messages).encode("utf-8"))
    return h.hexdigest()


def cache_get(cache_key: tuple) -> Optional[dict]:
    if not is_enabled():
        return None
    try:
        conn = _connect()
        key = _hash_key(cache_key)
        with _DB_LOCK:
            row = conn.execute(
                "SELECT payload_json FROM llm_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])
    except Exception as e:  # noqa: BLE001
        logger.debug("cache_get failed: %s", e)
        return None


def cache_put(cache_key: tuple, payload: Any) -> None:
    if not is_enabled():
        return
    try:
        conn = _connect()
        key = _hash_key(cache_key)
        agent, label, _messages, schema_name = cache_key
        # payload must be JSON-serializable — the FallbackLLM caller passes
        # a dict from pydantic .model_dump(), which is safe.
        payload_json = json.dumps(payload, default=str, sort_keys=True)
        with _DB_LOCK:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, agent, label, schema, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, agent, label, schema_name, payload_json),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("cache_put failed: %s", e)


def cache_stats() -> dict:
    """Small diagnostic — rows per agent, total DB size."""
    if not is_enabled():
        return {"enabled": False}
    try:
        conn = _connect()
        with _DB_LOCK:
            per_agent = dict(conn.execute(
                "SELECT agent, COUNT(*) FROM llm_cache GROUP BY agent"
            ).fetchall())
            total = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
        size_bytes = _db_path().stat().st_size if _db_path().exists() else 0
        return {
            "enabled": True, "path": str(_db_path()),
            "total_rows": total, "per_agent": per_agent,
            "size_bytes": size_bytes,
        }
    except Exception as e:  # noqa: BLE001
        return {"enabled": True, "error": str(e)}
