"""Override log — append-only record of 'I disagree with the committee'.

Every time the user clicks "I disagree" on a committee verdict, one line
lands in `logs/overrides.jsonl` with the committee's call, the user's
counter-call, and (crucially) the user's reason. That log is itself a
research dataset:

  - Over N months, is the user better than the committee?
  - Do overrides cluster on ticker, regime, or confidence bucket?
  - Does the user override more HOLDs (impatience) or more BUYs (caution)?

None of those questions can be answered without an audit trail. This
module is that audit trail. Read-only after write — no edits, no deletes.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERRIDES_PATH = REPO_ROOT / "logs" / "overrides.jsonl"


def record_override(
    ticker: str,
    as_of: str,
    committee_action: Optional[str],
    committee_confidence: Optional[float],
    user_action: str,
    user_reason: str,
    run_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
    journal_path: Optional[str] = None,
) -> dict:
    """Append one override record. Returns the stored dict (with id + ts)."""
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": str(uuid.uuid4()),
        "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticker": ticker.upper(),
        "as_of": as_of,
        "committee_action": committee_action,
        "committee_confidence": committee_confidence,
        "user_action": user_action,
        "user_reason": user_reason,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "journal_path": journal_path,
    }
    with open(OVERRIDES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


def list_overrides(limit: int = 200, ticker: Optional[str] = None) -> list[dict]:
    """Return the most recent overrides (newest first). Lightweight — the
    file is JSONL and overrides aren't high-volume."""
    if not OVERRIDES_PATH.exists():
        return []
    rows: list[dict] = []
    try:
        for line in OVERRIDES_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ticker and str(r.get("ticker") or "").upper() != ticker.upper():
                continue
            rows.append(r)
    except OSError as e:
        logger.warning("overrides: read failed: %s", e)
        return []
    rows.reverse()
    return rows[:limit]
