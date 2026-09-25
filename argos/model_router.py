"""
Model Router — per-agent LLM provider dispatch.

Design goal: keep each agent's quota bucket independent by spreading calls
across multiple free-tier providers. Only fundamentals + sentiment stay on
Gemini (Google Search grounding is unique to Gemini among free providers);
the rest go to Groq / Cerebras / SambaNova / OpenRouter / Ollama when their
API keys are set, and fall back to Gemini otherwise.

See ARGOS_FREE_LLM_PROVIDERS.md for the full survey and per-agent rationale.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)


Provider = Literal[
    "gemini",       # google-genai — grounded search available
    "groq",         # Groq LPU (sub-1s Llama 3.3 70B)
    "cerebras",     # Cerebras wafer-scale
    "sambanova",    # SambaNova (405B free)
    "openrouter",   # OpenRouter :free suffix models
    "nvidia",       # NVIDIA NIM (1000 credits/key)
    "ollama",       # Local via Ollama
]


# Agents that need Google Search grounding for real-time web data.
GROUNDED_AGENTS = {"fundamentals", "sentiment"}


@dataclass(frozen=True)
class ModelConfig:
    provider: Provider
    api_key_env: str
    model_name: str
    base_url: Optional[str] = None
    grounded: bool = False
    # Ordered list of fallback ModelConfigs to try on 429/503/quota
    # (each element is another ModelConfig). Kept as a tuple so the dataclass
    # stays hashable/frozen.
    fallbacks: tuple = field(default_factory=tuple)


# ── Named model shortcuts ───────────────────────────────────────────────────
# Keep model IDs in one place so provider retirements are a one-line fix.
# Verified live 2026-09-23 with the user's keys.
GEMINI_FLASH = "gemini-flash-latest"
GEMINI_PRO = "gemini-flash-latest"  # pro tier quota-exhausted on free; alias to flash
# Groq currently hosts OpenAI's open-weights models + Qwen (Llama-3.x retired).
GROQ_GPT_OSS_120B = "openai/gpt-oss-120b"      # strongest free reasoner on Groq
GROQ_GPT_OSS_20B = "openai/gpt-oss-20b"        # faster, still 20B
GROQ_QWEN_27B = "qwen/qwen3.8-27b"
CEREBRAS_GPT_OSS_120B = "gpt-oss-120b"        # verified live on Cerebras 2026-09-24
CEREBRAS_QWEN_27B = "qwen-3.8-27b"
CEREBRAS_LLAMA_70B = CEREBRAS_GPT_OSS_120B    # legacy alias — Llama was retired here
SAMBANOVA_DEEPSEEK_V32 = "DeepSeek-V3.2"    # biggest free reasoner on SambaNova today
SAMBANOVA_LLAMA_70B = "Meta-Llama-3.3-70B-Instruct"
SAMBANOVA_LLAMA_405B = SAMBANOVA_DEEPSEEK_V32  # legacy alias — 405B retired
OPENROUTER_NEMOTRON_ULTRA = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_QWEN = "qwen/qwen3.8-27b:free"


# Provider base URLs for OpenAI-compatible providers (used with langchain-openai).
PROVIDER_BASE_URL = {
    "openrouter": "https://openrouter.ai/api/v1",
    "cerebras":   "https://api.cerebras.ai/v1",
    "sambanova":  "https://api.sambanova.ai/v1",
    "nvidia":     "https://integrate.api.nvidia.com/v1",
    "ollama":     "http://localhost:11434/v1",
}


# Convenience constructors
def _gemini_cfg(grounded: bool = False, key_env: str = "GEMINI_PROJECT_A_API_KEY",
                model: str = GEMINI_FLASH) -> ModelConfig:
    return ModelConfig(provider="gemini", api_key_env=key_env, model_name=model,
                       grounded=grounded)

def _groq_cfg(model: str = GROQ_GPT_OSS_120B) -> ModelConfig:
    return ModelConfig(provider="groq", api_key_env="GROQ_API_KEY", model_name=model)

def _cerebras_cfg(model: str = CEREBRAS_GPT_OSS_120B) -> ModelConfig:
    return ModelConfig(provider="cerebras", api_key_env="CEREBRAS_API_KEY",
                       model_name=model, base_url=PROVIDER_BASE_URL["cerebras"])

def _sambanova_cfg(model: str = SAMBANOVA_DEEPSEEK_V32) -> ModelConfig:
    return ModelConfig(provider="sambanova", api_key_env="SAMBANOVA_API_KEY",
                       model_name=model, base_url=PROVIDER_BASE_URL["sambanova"])

def _openrouter_cfg(model: str = OPENROUTER_QWEN) -> ModelConfig:
    return ModelConfig(provider="openrouter", api_key_env="OPENROUTER_API_KEY",
                       model_name=model, base_url=PROVIDER_BASE_URL["openrouter"])


# Fallback to Gemini flash when the primary provider is unavailable so no
# agent silently drops out of the pipeline.
_GEMINI_FALLBACK = _gemini_cfg(key_env="GEMINI_PROJECT_A_API_KEY", model=GEMINI_FLASH)


# ── Per-agent routing table ─────────────────────────────────────────────────
# Fundamentals + Sentiment stay on Gemini because Google Search grounding is
# unique to Gemini among the free providers surveyed. The rest spread across
# providers so a single daily/RPM cap can't stall the whole pipeline.
AGENT_MODEL_MAP: dict[str, ModelConfig] = {
    # ── Grounded analysts (Gemini preferred for Google Search grounding) ────
    # Cascade through THREE Gemini project keys (A, B, C) to triple the daily
    # free-tier quota (each key gets its own 20 req/day/model). Groq fallback
    # is UN-grounded but keeps the graph alive when all Gemini quotas die.
    "fundamentals": ModelConfig(
        provider="gemini", api_key_env="GEMINI_PROJECT_A_API_KEY",
        model_name=GEMINI_FLASH, grounded=True,
        fallbacks=(_gemini_cfg(grounded=True,  key_env="GEMINI_PROJECT_B_API_KEY"),
                   _gemini_cfg(grounded=True,  key_env="GEMINI_PROJECT_C_API_KEY"),
                   _gemini_cfg(grounded=False, key_env="GEMINI_PROJECT_A_API_KEY"),
                   _groq_cfg(), _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA)),
    ),
    "sentiment": ModelConfig(
        provider="gemini", api_key_env="GEMINI_PROJECT_A_API_KEY",
        model_name=GEMINI_FLASH, grounded=True,
        fallbacks=(_gemini_cfg(grounded=True,  key_env="GEMINI_PROJECT_B_API_KEY"),
                   _gemini_cfg(grounded=True,  key_env="GEMINI_PROJECT_C_API_KEY"),
                   _gemini_cfg(grounded=False, key_env="GEMINI_PROJECT_A_API_KEY"),
                   _groq_cfg(), _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA)),
    ),

    # ── Technicals: fast, deterministic-ish; Groq is sub-1s
    "technicals": ModelConfig(
        provider="groq", api_key_env="GROQ_API_KEY", model_name=GROQ_GPT_OSS_120B,
        fallbacks=(_openrouter_cfg(), _GEMINI_FALLBACK),
    ),

    # ── Debate: SYMMETRIC model for Bull and Bear (U1 upgrade).
    # The 44-date backtest revealed Bear (Nemotron-550B) systematically scored
    # 0.30+ higher confidence than Bull (gpt-oss-20b) on 43/44 dates — that's
    # a model-provider prior, not the evidence. Fix: both use the same model
    # so any confidence gap is purely prompt/evidence driven.
    # Same fallback order for both keeps the symmetry all the way down.
    "bull": ModelConfig(
        provider="groq", api_key_env="GROQ_API_KEY", model_name=GROQ_GPT_OSS_120B,
        fallbacks=(_openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),
    "bear": ModelConfig(
        provider="groq", api_key_env="GROQ_API_KEY", model_name=GROQ_GPT_OSS_120B,
        fallbacks=(_openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),
    # NEW: blind judge — reads the debate transcript with side labels REMOVED
    # and picks a winner. Runs on a DIFFERENT provider (Nemotron-550B) so its
    # pattern of thinking is genuinely independent from the debating models.
    "judge": ModelConfig(
        provider="openrouter", api_key_env="OPENROUTER_API_KEY",
        model_name=OPENROUTER_NEMOTRON_ULTRA, base_url=PROVIDER_BASE_URL["openrouter"],
        fallbacks=(_groq_cfg(), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),

    # U11: Exit Manager — runs FIRST when a position is open, decides to
    # hold / exit / tighten. Fast + deterministic — Groq's 120B is fine.
    "exit_manager": ModelConfig(
        provider="groq", api_key_env="GROQ_API_KEY", model_name=GROQ_GPT_OSS_120B,
        fallbacks=(_openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),

    # ── Risk Manager: veto call — biggest free reasoner available.
    # OpenRouter Nemotron-550B preferred (largest OSS reasoner on true-free tier);
    # SambaNova DeepSeek-V3.2 is a strong upgrade if a paid tier becomes available.
    "risk_manager": ModelConfig(
        provider="openrouter", api_key_env="OPENROUTER_API_KEY",
        model_name=OPENROUTER_NEMOTRON_ULTRA, base_url=PROVIDER_BASE_URL["openrouter"],
        fallbacks=(_sambanova_cfg(SAMBANOVA_DEEPSEEK_V32),  # skipped if key unset / 402
                   _groq_cfg(), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),

    # ── Portfolio Manager: fast final synthesis
    "portfolio_manager": ModelConfig(
        provider="groq", api_key_env="GROQ_API_KEY", model_name=GROQ_GPT_OSS_120B,
        fallbacks=(_openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA), _openrouter_cfg(), _GEMINI_FALLBACK),
    ),
}


def get_model_config(agent_name: str) -> ModelConfig:
    return AGENT_MODEL_MAP[agent_name]


# ── LLM instantiation ───────────────────────────────────────────────────────


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
            logger.warning("Grounded search bind failed for %s: %s (proceeding without)",
                           cfg.model_name, e)
    return llm


def _make_groq(cfg: ModelConfig):
    from langchain_groq import ChatGroq
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise EnvironmentError(f"Missing {cfg.api_key_env}")
    # Groq gpt-oss ships with built-in tools (browser.search, code interpreter).
    # For structured output we must forbid the model from calling them —
    # otherwise every third response comes back as an unexpected tool call.
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
    logger.info("LLM: %s / %s", cfg.provider, cfg.model_name)
    return fn(cfg)


class FallbackLLM:
    """LLM shim that walks a list of underlying LLMs on runtime failure.

    Every agent's `get_llm()` returns one of these. The nodes call
    `.with_structured_output(Schema).invoke(msgs)` exactly as before — the
    wrapper transparently retries the same call on each configured backup
    provider when the primary raises (429/503/quota/timeout/etc.).

    This is what makes the multi-provider router actually resilient: a
    provider going dark mid-backtest just cascades to the next.
    """

    def __init__(self, llms, labels):
        self._llms = llms
        self._labels = labels
        self._structured_schema = None
        self._structured_kwargs: dict = {}
        self._binds: list[tuple[tuple, dict]] = []

    def _clone(self) -> "FallbackLLM":
        clone = FallbackLLM(self._llms, self._labels)
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

        Empirically:
        - Gemini: default (tool-calling) is native + reliable.
        - Groq gpt-oss: `json_mode` rejects any output that isn't strict
          JSON (chatty preamble, code fences, wrapped tool calls). Use
          `json_schema` instead — Groq enforces the shape server-side, so
          the model is CONSTRAINED to emit valid schema-matching JSON
          instead of being validated after the fact.
        - OpenRouter / SambaNova / Cerebras (all langchain-openai): same
          reasoning; `json_schema` is the reliable choice.
        """
        kwargs = dict(user_kwargs)
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
        last_err: Exception | None = None
        for i, (llm, label) in enumerate(zip(self._llms, self._labels)):
            try:
                inner = self._prepare(llm, label)
                return inner.invoke(messages, **kwargs)
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
    return FallbackLLM(built, labels)
