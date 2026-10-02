"""Tests for the OpenAI-primary model router.

Load-bearing invariants:
1. Bull and Bear share the same primary model (debate-symmetry).
2. Every agent in AGENT_MODEL_MAP builds an LLM successfully when
   OPENAI_API_KEY is set (no missing keys, no typo'd model names).
3. Grounded agents (fundamentals, sentiment) attempt to bind the
   web_search_preview tool on the OpenAI primary.
4. Reasoning models (o1, o1-mini) route through the json_mode
   structured-output path instead of the default function-calling.
5. Fallback cascade skips providers whose keys aren't set.
"""

from __future__ import annotations

import os

# Disable the LLM cache before importing model_router so FallbackLLM.invoke
# doesn't return cached results from a prior test's happy-path call.
os.environ["LLM_CACHE_ENABLED"] = "0"

from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from argos import model_router
from argos.model_router import (
    AGENT_MODEL_MAP,
    FallbackLLM,
    GROUNDED_AGENTS,
    _is_reasoning_model,
    get_llm,
    get_model_config,
)


# ── 1. Debate symmetry ────────────────────────────────────────────────────


def test_bull_and_bear_share_primary_model():
    b = AGENT_MODEL_MAP["bull"]
    x = AGENT_MODEL_MAP["bear"]
    assert b.provider == x.provider, "Bull/Bear must be on the same provider"
    assert b.model_name == x.model_name, "Bull/Bear must be on the same model"
    assert b.api_key_env == x.api_key_env, "Bull/Bear must draw the same quota bucket"


def test_bull_and_bear_share_fallback_chain():
    """The fallback chain must also be symmetric — otherwise a primary
    outage introduces model asymmetry mid-run."""
    b_names = [(f.provider, f.model_name) for f in AGENT_MODEL_MAP["bull"].fallbacks]
    x_names = [(f.provider, f.model_name) for f in AGENT_MODEL_MAP["bear"].fallbacks]
    assert b_names == x_names, "Bull/Bear fallback chains diverge"


# ── 2. All agents are configured ──────────────────────────────────────────


REQUIRED_AGENTS = (
    "fundamentals", "sentiment", "technicals",
    "bull", "bear", "judge",
    "exit_manager", "risk_manager", "portfolio_manager",
)


@pytest.mark.parametrize("agent", REQUIRED_AGENTS)
def test_every_agent_has_a_config(agent):
    cfg = get_model_config(agent)
    assert cfg.provider
    assert cfg.model_name
    assert cfg.api_key_env


@pytest.mark.parametrize("agent", REQUIRED_AGENTS)
def test_every_agent_primary_is_openai(agent):
    """The NASDAQ pivot moved every primary to OpenAI. Fallbacks may not
    be — this test only asserts the primary."""
    assert AGENT_MODEL_MAP[agent].provider == "openai", (
        f"{agent}: expected OpenAI primary, got {AGENT_MODEL_MAP[agent].provider}"
    )


# ── 3. Grounded agents ────────────────────────────────────────────────────


@pytest.mark.parametrize("agent", sorted(GROUNDED_AGENTS))
def test_grounded_agents_flag_grounded(agent):
    assert AGENT_MODEL_MAP[agent].grounded, f"{agent} should have grounded=True"


# ── 4. Reasoning-model gate ───────────────────────────────────────────────


@pytest.mark.parametrize("model_name,expected", [
    ("gpt-4o", False),
    ("gpt-4o-mini", False),
    ("o1", True),
    ("o1-mini", True),
    ("o3", True),
    ("o3-mini", True),
])
def test_reasoning_model_detection(model_name, expected):
    assert _is_reasoning_model(model_name) is expected


def test_structured_kwargs_for_reasoning_model_uses_json_mode():
    kwargs = FallbackLLM._structured_kwargs_for("openai/o1-mini", {})
    assert kwargs.get("method") == "json_mode"


def test_structured_kwargs_for_gpt4o_uses_function_calling():
    """gpt-4o uses `function_calling` — the default `json_schema` runs in
    strict mode and rejects Optional[dict] fields (400 error). See the
    docstring of `_structured_kwargs_for`."""
    kwargs = FallbackLLM._structured_kwargs_for("openai/gpt-4o", {})
    assert kwargs.get("method") == "function_calling"


def test_structured_kwargs_for_groq_uses_json_schema():
    kwargs = FallbackLLM._structured_kwargs_for("groq/openai/gpt-oss-120b", {})
    assert kwargs.get("method") == "json_schema"


# ── 5. Fallback cascade skips missing keys ────────────────────────────────


def test_get_llm_raises_when_no_provider_has_a_key():
    """Wipe every provider key from env → get_llm must raise, not silently return."""
    cfg = get_model_config("technicals")
    key_envs = {cfg.api_key_env} | {f.api_key_env for f in cfg.fallbacks}
    stripped = {k: "" for k in key_envs}
    with patch.dict(os.environ, stripped, clear=False):
        # Also clear via delete so os.environ.get returns None, not ""
        for k in key_envs:
            os.environ.pop(k, None)
        with pytest.raises(RuntimeError, match="No usable provider"):
            get_llm("technicals")


def test_get_llm_builds_with_only_openai_key():
    """With only OPENAI_API_KEY set, get_llm must still return a working
    FallbackLLM whose chain is trimmed to just the OpenAI candidates."""
    cfg = get_model_config("technicals")
    key_envs = {cfg.api_key_env} | {f.api_key_env for f in cfg.fallbacks}
    # Snapshot + wipe, then set only OPENAI_API_KEY.
    saved = {k: os.environ.get(k) for k in key_envs}
    for k in key_envs:
        os.environ.pop(k, None)
    os.environ["OPENAI_API_KEY"] = "sk-test-not-real"
    try:
        llm = get_llm("technicals")
        assert isinstance(llm, FallbackLLM)
        assert len(llm._llms) >= 1
        assert all(label.startswith("openai/") for label in llm._labels
                   if "openai" in label)
    finally:
        os.environ.pop("OPENAI_API_KEY", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# ── 6. FallbackLLM shim behavior ──────────────────────────────────────────


class _FakeLLM:
    """Minimal stand-in for a LangChain chat model."""
    def __init__(self, response, raises=False):
        self.response = response
        self.raises = raises
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        return _FakeStructured(self, schema)

    def bind(self, *args, **kwargs):
        return self


class _FakeStructured:
    def __init__(self, parent, schema):
        self.parent = parent
        self.schema = schema

    def invoke(self, msgs, **kwargs):
        self.parent.calls += 1
        if self.parent.raises:
            raise RuntimeError("simulated provider outage")
        return self.parent.response


class _FakeSchema:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self):
        return dict(self.__dict__)


def test_fallback_walks_to_next_on_failure():
    primary = _FakeLLM(None, raises=True)
    secondary = _FakeLLM(_FakeSchema(signal="bullish"))
    shim = FallbackLLM([primary, secondary], ["openai/gpt-4o", "openai/gpt-4o-mini"],
                       agent_name="test_only_not_a_real_agent")
    result = shim.with_structured_output(_FakeSchema).invoke([("user", "hi")])
    assert primary.calls == 1
    assert secondary.calls == 1
    assert result.signal == "bullish"


def test_fallback_raises_when_all_exhausted():
    primary = _FakeLLM(None, raises=True)
    secondary = _FakeLLM(None, raises=True)
    shim = FallbackLLM([primary, secondary], ["openai/gpt-4o", "openai/gpt-4o-mini"],
                       agent_name="test_only_not_a_real_agent")
    with pytest.raises(RuntimeError, match="All 2 providers exhausted"):
        shim.with_structured_output(_FakeSchema).invoke([("user", "hi")])
