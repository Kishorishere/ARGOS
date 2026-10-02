"""
Model Router — per-agent LLM provider dispatch.

Design goal: keep each agent's quota bucket independent by spreading calls
across multiple providers. As of the NASDAQ pivot the primary tier is
OpenAI (paid): different tiers per role, but Bull ↔ Bear MUST share the
same model so any confidence gap is evidence-driven, not model-driven.

Fallbacks: Groq (free-tier LPU) then Gemini flash — the graph should never
silently drop an agent, and the FallbackLLM shim walks the cascade on
429/503/quota/timeout.

See ARGOS_FREE_LLM_PROVIDERS.md for the older free-tier survey (now the
fallback layer only).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)


Provider = Literal[
    "openai",       # OpenAI Responses/Chat Completions — primary
    "gemini",       # google-genai — grounded search available (fallback)
    "groq",         # Groq LPU (sub-1s Llama 3.3 70B) — fallback
    "cerebras",     # Cerebras wafer-scale
    "sambanova",    # SambaNova
    "openrouter",   # OpenRouter :free suffix models
    "nvidia",       # NVIDIA NIM
    "ollama",       # Local via Ollama
]


# Agents that need live-web grounding (news, filings, guidance).
GROUNDED_AGENTS = {"fundamentals", "sentiment"}


@dataclass(frozen=True)
class ModelConfig:
    provider: Provider
    api_key_env: str
    model_name: str
    base_url: Optional[str] = None
    grounded: bool = False
    # Ordered list of fallback ModelConfigs to try on 429/503/quota
    fallbacks: tuple = field(default_factory=tuple)


# ── Named model shortcuts ───────────────────────────────────────────────────
# Keep model IDs in one place so provider retirements are a one-line fix.
# OpenAI (NASDAQ-pivot primary, 2026-09):
OPENAI_GPT_4O = "gpt-4o"
OPENAI_GPT_4O_MINI = "gpt-4o-mini"
OPENAI_O1 = "o1"
OPENAI_O1_MINI = "o1-mini"

# Gemini (fallback tier — grounded search unique on free providers)
GEMINI_FLASH = "gemini-flash-latest"
GEMINI_PRO = "gemini-flash-latest"  # pro quota exhausted on free; alias to flash

# Groq (fallback tier)
GROQ_GPT_OSS_120B = "openai/gpt-oss-120b"
GROQ_GPT_OSS_20B = "openai/gpt-oss-20b"

# OpenRouter (fallback tier)
OPENROUTER_NEMOTRON_ULTRA = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_QWEN = "qwen/qwen3.8-27b:free"

# SambaNova / Cerebras kept for completeness — not in default fallback chain
SAMBANOVA_DEEPSEEK_V32 = "DeepSeek-V3.2"
CEREBRAS_GPT_OSS_120B = "gpt-oss-120b"


# Provider base URLs for OpenAI-compatible providers.
PROVIDER_BASE_URL = {
    "openrouter": "https://openrouter.ai/api/v1",
    "cerebras":   "https://api.cerebras.ai/v1",
    "sambanova":  "https://api.sambanova.ai/v1",
    "nvidia":     "https://integrate.api.nvidia.com/v1",
    "ollama":     "http://localhost:11434/v1",
}


# ── Convenience constructors ────────────────────────────────────────────────

def _openai_cfg(model: str = OPENAI_GPT_4O, grounded: bool = False) -> ModelConfig:
    return ModelConfig(provider="openai", api_key_env="OPENAI_API_KEY",
                       model_name=model, grounded=grounded)


def _gemini_cfg(grounded: bool = False, key_env: str = "GEMINI_PROJECT_A_API_KEY",
                model: str = GEMINI_FLASH) -> ModelConfig:
    return ModelConfig(provider="gemini", api_key_env=key_env, model_name=model,
                       grounded=grounded)


def _groq_cfg(model: str = GROQ_GPT_OSS_120B) -> ModelConfig:
    return ModelConfig(provider="groq", api_key_env="GROQ_API_KEY", model_name=model)


def _openrouter_cfg(model: str = OPENROUTER_QWEN) -> ModelConfig:
    return ModelConfig(provider="openrouter", api_key_env="OPENROUTER_API_KEY",
                       model_name=model, base_url=PROVIDER_BASE_URL["openrouter"])


# Shared cheap OpenAI fallback so a primary model's outage doesn't kill the graph.
_OPENAI_MINI_FALLBACK = _openai_cfg(OPENAI_GPT_4O_MINI)
_GROQ_FALLBACK = _groq_cfg()
_GEMINI_FALLBACK = _gemini_cfg(model=GEMINI_FLASH)


# ── Per-agent routing table ─────────────────────────────────────────────────
# Debate symmetry is the load-bearing invariant here: Bull and Bear MUST share
# the same primary model, same temperature, same fallback chain, so any
# confidence gap between them is evidence-driven, not provider-driven.
# See U1 note in the git log (2026-09).
AGENT_MODEL_MAP: dict[str, ModelConfig] = {
    # ── Grounded analysts (OpenAI + web_search_preview tool when supported) ─
    # `grounded=True` triggers a best-effort tool bind in _make_openai; if the
    # installed langchain-openai version doesn't support Responses-API tools,
    # it silently proceeds without grounding (the node prompt still tells the
    # model to reason strictly as-of the given date).
    "fundamentals": _openai_cfg(OPENAI_GPT_4O, grounded=True).__class__(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O, grounded=True,
        fallbacks=(_openai_cfg(OPENAI_GPT_4O_MINI, grounded=True),
                   _gemini_cfg(grounded=True, key_env="GEMINI_PROJECT_A_API_KEY"),
                   _gemini_cfg(grounded=True, key_env="GEMINI_PROJECT_B_API_KEY"),
                   _groq_cfg(), _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA)),
    ),
    "sentiment": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O, grounded=True,
        fallbacks=(_openai_cfg(OPENAI_GPT_4O_MINI, grounded=True),
                   _gemini_cfg(grounded=True, key_env="GEMINI_PROJECT_A_API_KEY"),
                   _gemini_cfg(grounded=True, key_env="GEMINI_PROJECT_B_API_KEY"),
                   _groq_cfg(), _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA)),
    ),

    # ── Technicals: fast, deterministic-ish; mini is enough
    "technicals": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O_MINI,
        fallbacks=(_groq_cfg(), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),

    # ── Debate: SYMMETRIC. Same model, same temp, same fallbacks.
    # Load-bearing: any asymmetry re-introduces the model-prior confidence gap
    # that the 44-date NEPSE backtest surfaced.
    "bull": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O,
        fallbacks=(_OPENAI_MINI_FALLBACK, _groq_cfg(),
                   _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA), _GEMINI_FALLBACK),
    ),
    "bear": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O,
        fallbacks=(_OPENAI_MINI_FALLBACK, _groq_cfg(),
                   _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA), _GEMINI_FALLBACK),
    ),

    # ── Judge: DIFFERENT model from debaters so its pattern of thinking is
    # independent. Debaters run on gpt-4o; judge runs on gpt-4o-mini
    # (smaller family sibling). The o1/o1-mini reasoning models are not
    # provisioned on this key (2026-09 verification), so a cross-family
    # judge would just cascade — gpt-4o-mini gives independence via a
    # different capacity tier without wasting a fallback hop.
    "judge": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O_MINI,
        fallbacks=(_openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA),
                   _groq_cfg(), _GEMINI_FALLBACK),
    ),

    # ── Exit Manager: runs FIRST when a position is open — fast + cheap
    "exit_manager": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O_MINI,
        fallbacks=(_groq_cfg(), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),

    # ── Risk Manager: veto call — biggest reasoner available on this key.
    # o1 is not provisioned (2026-09), so use gpt-4o (same tier as debaters
    # but larger prompt budget for the full transcript + risk checklist).
    "risk_manager": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O,
        fallbacks=(_openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA),
                   _groq_cfg(), _GEMINI_FALLBACK),
    ),

    # ── Portfolio Manager: final synthesis — gpt-4o for judgment quality
    "portfolio_manager": ModelConfig(
        provider="openai", api_key_env="OPENAI_API_KEY",
        model_name=OPENAI_GPT_4O,
        fallbacks=(_OPENAI_MINI_FALLBACK, _groq_cfg(),
                   _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA), _GEMINI_FALLBACK),
    ),
}


# ── Symmetry invariant (fail fast at import if it drifts) ───────────────────
assert (
    AGENT_MODEL_MAP["bull"].model_name == AGENT_MODEL_MAP["bear"].model_name
    and AGENT_MODEL_MAP["bull"].provider == AGENT_MODEL_MAP["bear"].provider
), "Bull and Bear must share the same primary model — see U1 (debate symmetry)."


def get_model_config(agent_name: str) -> ModelConfig:
    return AGENT_MODEL_MAP[agent_name]


# ── LLM instantiation ───────────────────────────────────────────────────────


# Reasoning models (o1, o3, o1-mini) reject `temperature` and `max_retries`
# kwargs — they only support default temperature=1.0.
_REASONING_PREFIXES = ("o1", "o3")


def _is_reasoning_model(model_name: str) -> bool:
    return any(model_name.startswith(p) for p in _REASONING_PREFIXES)


def _make_openai(cfg: ModelConfig):
    """OpenAI primary path. Grounded roles best-effort bind web_search."""
    from langchain_openai import ChatOpenAI
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise EnvironmentError(f"Missing {cfg.api_key_env}")

    kwargs: dict = {"model": cfg.model_name, "api_key": api_key, "timeout": 60}
    if not _is_reasoning_model(cfg.model_name):
        kwargs["temperature"] = 0.3
        kwargs["max_retries"] = 3

    llm = ChatOpenAI(**kwargs)

    if cfg.grounded:
        # NOTE: We intentionally do NOT bind web_search_preview here.
        # The tool-call channel conflicts with `method="function_calling"`
        # structured output (the model picks web_search instead of emitting
        # the schema function). For the 2024 NASDAQ backtest the model's
        # training data covers the target dates well; the prompt tells it
        # to reason strictly as-of {as_of_date}. Revisit when we need
        # live-web grounding for a forward-looking mode.
        logger.debug("OpenAI grounded=True on %s — using training-data "
                     "reasoning (web_search bind disabled to avoid conflict "
                     "with function-calling structured output)", cfg.model_name)
    return llm


def _make_gemini(cfg: ModelConfig):
    from langchain_google_genai import ChatGoogleGenerativeAI
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise EnvironmentError(f"Missing {cfg.api_key_env}")
    max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", "5"))
    timeout = int(os.environ.get("GEMINI_TIMEOUT_S", "60"))
    llm = ChatGoogleGenerativeAI(
        model=cfg.model_name, google_api_key=api_key,
        temperature=0.3, max_retries=max_retries, timeout=timeout,
    )
    if cfg.grounded:
        try:
            from google.genai.types import Tool, GoogleSearch
            llm = llm.bind(tools=[Tool(google_search=GoogleSearch())])
        except Exception as e:  # noqa: BLE001
            logger.warning("Gemini grounded search bind failed for %s: %s "
                           "(proceeding without)", cfg.model_name, e)
    return llm


def _make_groq(cfg: ModelConfig):
    from langchain_groq import ChatGroq
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise EnvironmentError(f"Missing {cfg.api_key_env}")
    return ChatGroq(
        model=cfg.model_name, api_key=api_key,
        temperature=0.3, max_retries=3, timeout=45,
        model_kwargs={"tool_choice": "none"},
    )


def _make_openai_compatible(cfg: ModelConfig):
    """OpenAI-compatible providers: OpenRouter, Cerebras, SambaNova, NVIDIA, Ollama."""
    from langchain_openai import ChatOpenAI
    api_key = os.environ.get(cfg.api_key_env) or "not-required"
    return ChatOpenAI(
        model=cfg.model_name, api_key=api_key, base_url=cfg.base_url,
        temperature=0.3, max_retries=3, timeout=45,
    )


_DISPATCH = {
    "openai":     _make_openai,
    "gemini":     _make_gemini,
    "groq":       _make_groq,
    "cerebras":   _make_openai_compatible,
    "sambanova":  _make_openai_compatible,
    "openrouter": _make_openai_compatible,
    "nvidia":     _make_openai_compatible,
    "ollama":     _make_openai_compatible,
}


def _make_llm_from_cfg(cfg: ModelConfig):
    fn = _DISPATCH[cfg.provider]
    logger.info("LLM: %s / %s%s", cfg.provider, cfg.model_name,
                " [grounded]" if cfg.grounded else "")
    return fn(cfg)


class FallbackLLM:
    """LLM shim that walks a list of underlying LLMs on runtime failure.

    Every agent's `get_llm()` returns one of these. The nodes call
    `.with_structured_output(Schema).invoke(msgs)` exactly as before — the
    wrapper transparently retries the same call on each configured backup
    provider when the primary raises (429/503/quota/timeout/etc.).
    """

    def __init__(self, llms, labels, agent_name: Optional[str] = None):
        self._llms = llms
        self._labels = labels
        self._agent_name = agent_name
        self._structured_schema = None
        self._structured_kwargs: dict = {}
        self._binds: list[tuple[tuple, dict]] = []

    def _clone(self) -> "FallbackLLM":
        clone = FallbackLLM(self._llms, self._labels, self._agent_name)
        clone._structured_schema = self._structured_schema
        clone._structured_kwargs = dict(self._structured_kwargs)
        clone._binds = list(self._binds)
        return clone

    def with_structured_output(self, schema, **kwargs):
        clone = self._clone()
        clone._structured_schema = schema
        clone._structured_kwargs = kwargs
        return clone

    def bind(self, *args, **kwargs):
        clone = self._clone()
        clone._binds.append((args, kwargs))
        return clone

    @staticmethod
    def _structured_kwargs_for(label: str, user_kwargs: dict) -> dict:
        """Pick the right `method` per provider.

        - OpenAI gpt-4o family: use `function_calling` — the default
          `json_schema` runs in STRICT mode which requires every nested
          object to explicitly declare `additionalProperties: false`. The
          `AnalystSignal` schema has an `Optional[dict]` field that fails
          this validation server-side (400 error, no retry helps). Function
          calling is lenient with the schema shape.
        - OpenAI o1/o3 reasoning models: no function-calling support today;
          use `json_mode` (schema described in the prompt).
        - Gemini: default (tool-calling) works.
        - Groq / OpenRouter / SambaNova / Cerebras: json_schema server-enforced.
        """
        kwargs = dict(user_kwargs)
        if label.startswith("openai/o1") or label.startswith("openai/o3"):
            kwargs.setdefault("method", "json_mode")
            return kwargs
        if label.startswith("openai/"):
            kwargs.setdefault("method", "function_calling")
            return kwargs
        if label.startswith("gemini/") or label.startswith("nvidia/"):
            return kwargs  # native structured output works
        kwargs.setdefault("method", "json_schema")
        return kwargs

    def _prepare(self, llm, label):
        for args, kwargs in self._binds:
            llm = llm.bind(*args, **kwargs)
        if self._structured_schema is not None:
            struct_kwargs = self._structured_kwargs_for(label, self._structured_kwargs)
            llm = llm.with_structured_output(self._structured_schema, **struct_kwargs)
        return llm

    def invoke(self, messages, **kwargs):
        # Cache lookup: only if agent_name is known and a cache is configured.
        from argos.llm_cache import cache_get, cache_put, is_enabled
        cache_key = None
        if is_enabled() and self._agent_name and self._structured_schema is not None:
            cache_key = (self._agent_name, self._labels[0] if self._labels else "?",
                         messages, self._structured_schema.__name__ if hasattr(self._structured_schema, "__name__") else str(self._structured_schema))
            hit = cache_get(cache_key)
            if hit is not None:
                logger.info("LLM cache HIT [%s / %s]", self._agent_name,
                            self._labels[0] if self._labels else "?")
                # Rehydrate to the pydantic schema so downstream .model_dump() works
                try:
                    return self._structured_schema(**hit)
                except Exception:
                    return hit

        last_err: Exception | None = None
        for i, (llm, label) in enumerate(zip(self._llms, self._labels)):
            try:
                inner = self._prepare(llm, label)
                result = inner.invoke(messages, **kwargs)
                if cache_key is not None:
                    try:
                        payload = result.model_dump() if hasattr(result, "model_dump") else result
                        cache_put(cache_key, payload)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("Cache put failed: %s", e)
                return result
            except Exception as e:  # noqa: BLE001
                last_err = e
                remaining = len(self._llms) - i - 1
                logger.warning(
                    "Provider %s failed on invoke (%d fallback%s left): %s",
                    label, remaining, "s" if remaining != 1 else "",
                    str(e).splitlines()[0][:200],
                )
        raise RuntimeError(
            f"All {len(self._llms)} providers exhausted. Last error: {last_err}"
        )


def get_llm(agent_name: str):
    """Return a FallbackLLM that transparently cycles through every configured
    provider for `agent_name` on runtime failure.

    Only providers whose API-key env var is set get built and included in the
    cascade — unset providers are skipped silently (with a log). Raises only
    if not a single provider in the chain has a key set.
    """
    cfg = get_model_config(agent_name)
    candidates = [cfg] + list(cfg.fallbacks)
    built: list = []
    labels: list = []
    for candidate in candidates:
        if not os.environ.get(candidate.api_key_env):
            logger.info("Skipping %s/%s for %s — missing %s",
                        candidate.provider, candidate.model_name, agent_name,
                        candidate.api_key_env)
            continue
        try:
            built.append(_make_llm_from_cfg(candidate))
            labels.append(f"{candidate.provider}/{candidate.model_name}")
        except Exception as e:  # noqa: BLE001
            logger.warning("Provider %s failed to build for %s: %s",
                           candidate.provider, agent_name, e)
    if not built:
        raise RuntimeError(
            f"No usable provider for agent {agent_name}. "
            f"Set at least one of: {', '.join(c.api_key_env for c in candidates)}."
        )
    return FallbackLLM(built, labels, agent_name=agent_name)
