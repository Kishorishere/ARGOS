"""
Model Router — per-agent LLM provider dispatch.

Design goal: keep each agent's quota bucket independent by spreading calls
across multiple Gemini API keys (one Google Cloud project per key = one
free-tier quota pool). Primary tier is Gemini. Fallbacks: Groq (free-tier
LPU) and OpenRouter (:free models) so the graph never silently drops an
agent — the FallbackLLM shim walks the cascade on 429/503/quota/timeout.

Bull ↔ Bear MUST share the same primary model (symmetry invariant) so any
confidence gap between them is evidence-driven, not model-driven.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)


Provider = Literal[
    "gemini",       # google-genai — primary (6 keys = 6 quota pools)
    "groq",         # Groq LPU (sub-1s Llama 3.3 70B) — fallback
    "openrouter",   # OpenRouter :free suffix models — fallback
    "cerebras",     # Cerebras wafer-scale
    "sambanova",    # SambaNova
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
# Gemini 3 pinned model IDs. Gemini 2.x is being deprecated/retired and the
# `-latest` aliases on 2.x route everyone to the same hot backend (503s on
# every key together). Pinning to Gemini 3 gives us a current, less-contended
# pool. Flash-lite kept as the per-agent in-chain fallback so a hot primary
# doesn't take out the whole run.
GEMINI_FLASH = "gemini-3.5-flash"
GEMINI_FLASH_LITE = "gemini-3.5-flash-lite"
# NOTE: gemini-3.1-pro-preview is 429 RESOURCE_EXHAUSTED on free-tier (preview
# tier has no free quota). Point PRO at the flash GA model until a billed key
# is wired up — same provider, free, sub-5s latency.
GEMINI_PRO = "gemini-3.5-flash"

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


# ── Gemini key pool ─────────────────────────────────────────────────────────
# Six independent Google Cloud projects = six free-tier quota buckets. The
# per-agent routing table below pins each agent to a primary key and lists
# the rest as fallbacks, so one agent's quota exhaustion doesn't take out
# the whole graph.
GEMINI_KEY_A    = "GEMINI_PROJECT_A_API_KEY"  # a.k.a. trader-1
GEMINI_KEY_B    = "GEMINI_PROJECT_B_API_KEY"  # a.k.a. trader-2
GEMINI_KEY_C    = "GEMINI_PROJECT_C_API_KEY"
GEMINI_KEY_OCR1 = "GEMINI_OCR_1_API_KEY"
GEMINI_KEY_OCR2 = "GEMINI_OCR_2_API_KEY"
GEMINI_KEY_OCR3 = "GEMINI_OCR_3_API_KEY"

ALL_GEMINI_KEYS = (
    GEMINI_KEY_A, GEMINI_KEY_B, GEMINI_KEY_C,
    GEMINI_KEY_OCR1, GEMINI_KEY_OCR2, GEMINI_KEY_OCR3,
)


# ── Convenience constructors ────────────────────────────────────────────────

def _gemini_cfg(key_env: str, model: str = GEMINI_FLASH,
                grounded: bool = False) -> ModelConfig:
    return ModelConfig(provider="gemini", api_key_env=key_env,
                       model_name=model, grounded=grounded)


def _gemini_chain(primary_key: str, model: str = GEMINI_FLASH,
                  grounded: bool = False) -> tuple:
    """Build a Gemini fallback chain that starts at `primary_key` and then
    walks every OTHER key in ALL_GEMINI_KEYS in a stable rotated order, then
    retries each key again with flash-lite (different model backend → avoids
    a 503-storm on the primary model taking out the whole run).
    """
    rotated = [k for k in ALL_GEMINI_KEYS if k != primary_key]
    primary_tier = tuple(_gemini_cfg(k, model=model, grounded=grounded) for k in rotated)
    lite_tier = tuple(_gemini_cfg(k, model=GEMINI_FLASH_LITE, grounded=grounded)
                      for k in [primary_key] + rotated)
    return primary_tier + lite_tier


def _groq_cfg(model: str = GROQ_GPT_OSS_120B) -> ModelConfig:
    return ModelConfig(provider="groq", api_key_env="GROQ_API_KEY", model_name=model)


def _openrouter_cfg(model: str = OPENROUTER_QWEN) -> ModelConfig:
    return ModelConfig(provider="openrouter", api_key_env="OPENROUTER_API_KEY",
                       model_name=model, base_url=PROVIDER_BASE_URL["openrouter"])


_GROQ_FALLBACK = _groq_cfg()
_OPENROUTER_FALLBACK = _openrouter_cfg(OPENROUTER_NEMOTRON_ULTRA)


def _build(primary_key: str, model: str, grounded: bool = False,
           tail: tuple = ()) -> ModelConfig:
    """Primary Gemini config + rotated Gemini key chain + tail (Groq/OR)."""
    primary = _gemini_cfg(primary_key, model=model, grounded=grounded)
    chain = _gemini_chain(primary_key, model=model, grounded=grounded) + tail
    return ModelConfig(
        provider="gemini", api_key_env=primary_key,
        model_name=model, grounded=grounded, fallbacks=chain,
    )


# ── Per-agent routing table ─────────────────────────────────────────────────
# Each agent is pinned to a distinct primary key so a single run spreads load
# across all six Gemini quota pools. On 429/quota the FallbackLLM walks the
# rotated key chain, then Groq + OpenRouter as deep fallbacks.
#
# Debate symmetry invariant: Bull and Bear MUST share the same primary model
# (asymmetry re-introduces the model-prior confidence gap we hit on NEPSE).
AGENT_MODEL_MAP: dict[str, ModelConfig] = {
    # Grounded analysts — flash with Google Search grounding
    "fundamentals": _build(GEMINI_KEY_OCR1, GEMINI_FLASH, grounded=True,
                           tail=(_GROQ_FALLBACK, _OPENROUTER_FALLBACK)),
    "sentiment":    _build(GEMINI_KEY_OCR2, GEMINI_FLASH, grounded=True,
                           tail=(_GROQ_FALLBACK, _OPENROUTER_FALLBACK)),

    # Fast/cheap flash agents
    "technicals":   _build(GEMINI_KEY_OCR3, GEMINI_FLASH,
                           tail=(_GROQ_FALLBACK, _OPENROUTER_FALLBACK)),
    "exit_manager": _build(GEMINI_KEY_C, GEMINI_FLASH,
                           tail=(_GROQ_FALLBACK, _OPENROUTER_FALLBACK)),

    # Pre-debate edge gate — explicitly cheapest tier. One structured call
    # per decision deciding whether the expensive bull/bear/judge/risk/PM
    # stack is worth running today. Flash-lite primary keeps the gate's own
    # cost under ~2k tokens/call, so even on every-day runs the gate pays
    # for itself in a single skipped debate.
    "edge_gate":    _build(GEMINI_KEY_C, GEMINI_FLASH_LITE,
                           tail=(_GROQ_FALLBACK, _OPENROUTER_FALLBACK)),

    # Debate — SYMMETRIC. Same primary model + same chain.
    "bull": _build(GEMINI_KEY_A, GEMINI_FLASH,
                   tail=(_GROQ_FALLBACK, _OPENROUTER_FALLBACK)),
    "bear": _build(GEMINI_KEY_A, GEMINI_FLASH,
                   tail=(_GROQ_FALLBACK, _OPENROUTER_FALLBACK)),

    # Judge — different key family from debaters so bias is independent
    "judge": _build(GEMINI_KEY_C, GEMINI_FLASH,
                    tail=(_OPENROUTER_FALLBACK, _GROQ_FALLBACK)),

    # Heavy reasoners — pro tier on the two trader keys
    "risk_manager":      _build(GEMINI_KEY_B, GEMINI_PRO,
                                tail=(_OPENROUTER_FALLBACK, _GROQ_FALLBACK)),
    "portfolio_manager": _build(GEMINI_KEY_B, GEMINI_PRO,
                                tail=(_OPENROUTER_FALLBACK, _GROQ_FALLBACK)),
}


# ── Symmetry invariant (fail fast at import if it drifts) ───────────────────
assert (
    AGENT_MODEL_MAP["bull"].model_name == AGENT_MODEL_MAP["bear"].model_name
    and AGENT_MODEL_MAP["bull"].provider == AGENT_MODEL_MAP["bear"].provider
    and AGENT_MODEL_MAP["bull"].api_key_env == AGENT_MODEL_MAP["bear"].api_key_env
), "Bull and Bear must share the same primary model + key — see debate symmetry."


def get_model_config(agent_name: str) -> ModelConfig:
    return AGENT_MODEL_MAP[agent_name]


# ── LLM instantiation ───────────────────────────────────────────────────────


def _make_gemini(cfg: ModelConfig):
    from langchain_google_genai import ChatGoogleGenerativeAI
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise EnvironmentError(f"Missing {cfg.api_key_env}")
    max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", "1"))
    timeout = int(os.environ.get("GEMINI_TIMEOUT_S", "20"))
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
    logger.info("LLM: %s / %s [key=%s]%s", cfg.provider, cfg.model_name,
                cfg.api_key_env, " [grounded]" if cfg.grounded else "")
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
        """Pick the right `method` per provider."""
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
        from argos.llm_cache import cache_get, cache_put, is_enabled
        from argos.token_usage import TokenCapture
        cache_key = None
        if is_enabled() and self._agent_name and self._structured_schema is not None:
            cache_key = (self._agent_name, self._labels[0] if self._labels else "?",
                         messages, self._structured_schema.__name__ if hasattr(self._structured_schema, "__name__") else str(self._structured_schema))
            hit = cache_get(cache_key)
            if hit is not None:
                logger.info("LLM cache HIT [%s / %s]", self._agent_name,
                            self._labels[0] if self._labels else "?")
                try:
                    return self._structured_schema(**hit)
                except Exception:
                    return hit

        last_err: Exception | None = None
        for i, (llm, label) in enumerate(zip(self._llms, self._labels)):
            try:
                inner = self._prepare(llm, label)
                call_kwargs = dict(kwargs)
                cfg = dict(call_kwargs.get("config") or {})
                existing_cbs = list(cfg.get("callbacks") or [])
                existing_cbs.append(TokenCapture(self._agent_name or "?", label))
                cfg["callbacks"] = existing_cbs
                call_kwargs["config"] = cfg
                result = inner.invoke(messages, **call_kwargs)
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
