"""Per-decision token usage tracking.

Design: a thread-local counter keyed on `agent/model_label`. The graph driver
calls `start_decision()` before `graph.invoke()`; each LLM call attaches a
`TokenCapture` callback that records `(input_tokens, output_tokens)` on
`on_llm_end`; `snapshot()` returns the aggregated usage for journalling.

Why callbacks instead of `with_structured_output(..., include_raw=True)`:
some fallback providers (OpenRouter free tier, older Gemini wrappers) don't
pass `include_raw` through cleanly. A callback is universal across langchain
chat models.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)
_local = threading.local()


def _ensure_bucket() -> dict:
    if not hasattr(_local, "tokens"):
        _local.tokens = defaultdict(lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        })
    return _local.tokens


def start_decision() -> None:
    """Clear the per-decision counter. Call before each `graph.invoke()`."""
    _local.tokens = defaultdict(lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
    })


def record(agent: str, label: str, input_tokens: int, output_tokens: int) -> None:
    bucket = _ensure_bucket()[f"{agent}/{label}"]
    bucket["input_tokens"] += int(input_tokens or 0)
    bucket["output_tokens"] += int(output_tokens or 0)
    bucket["total_tokens"] += int((input_tokens or 0) + (output_tokens or 0))
    bucket["calls"] += 1


def snapshot() -> dict:
    """Return a plain-dict snapshot of this decision's token usage.

    Shape: {"<agent>/<label>": {input_tokens, output_tokens, total_tokens, calls}, ...}
    """
    if not hasattr(_local, "tokens"):
        return {}
    return {k: dict(v) for k, v in _local.tokens.items()}


def totals() -> dict:
    """Aggregate across all agents in the current decision."""
    s = snapshot()
    inp = sum(v["input_tokens"] for v in s.values())
    out = sum(v["output_tokens"] for v in s.values())
    calls = sum(v["calls"] for v in s.values())
    return {"input_tokens": inp, "output_tokens": out,
            "total_tokens": inp + out, "calls": calls}


# ── LangChain callback ─────────────────────────────────────────────────────

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover
    BaseCallbackHandler = object  # type: ignore[misc,assignment]


class TokenCapture(BaseCallbackHandler):  # type: ignore[misc]
    """Attached per-invoke to the underlying LLM. Pulls usage_metadata off
    the AIMessage returned by `on_llm_end`."""

    def __init__(self, agent: str, label: str):
        self.agent = agent
        self.label = label

    def on_llm_end(self, response: Any, **kwargs) -> None:  # type: ignore[override]
        try:
            total_in = 0
            total_out = 0
            for gen_list in getattr(response, "generations", []) or []:
                for gen in gen_list:
                    # ChatGeneration carries .message, LLMResult generation does not.
                    msg = getattr(gen, "message", None)
                    um = getattr(msg, "usage_metadata", None) if msg else None
                    if um:
                        total_in += int(um.get("input_tokens", 0) or 0)
                        total_out += int(um.get("output_tokens", 0) or 0)
                        continue
                    # Fallback: look in generation_info -> token_usage
                    gi = getattr(gen, "generation_info", None) or {}
                    tu = gi.get("token_usage") or {}
                    if tu:
                        total_in += int(tu.get("prompt_tokens", 0) or 0)
                        total_out += int(tu.get("completion_tokens", 0) or 0)
            # Also check llm_output (OpenAI historically puts totals here)
            llm_out = getattr(response, "llm_output", None) or {}
            if not total_in and not total_out and llm_out.get("token_usage"):
                tu = llm_out["token_usage"]
                total_in = int(tu.get("prompt_tokens", 0) or 0)
                total_out = int(tu.get("completion_tokens", 0) or 0)
            if total_in or total_out:
                record(self.agent, self.label, total_in, total_out)
        except Exception as e:  # noqa: BLE001
            logger.debug("TokenCapture.on_llm_end failed: %s", e)
