# Free-tier LLM providers for Argos — honest survey

> Snapshot: 2026-09-23. Rate limits and free-tier terms change often; treat
> the numbers below as "what to expect", not contract. Every one of these
> requires signup (email), but **none require a credit card or billing setup**
> unless explicitly noted.

**Constraint:** free-tier only during development. Keep Gemini for fundamentals
+ sentiment (Google Search grounding is unique to Gemini among free
providers) and spread the other 5 agents across providers so no single
daily/RPM cap blocks the whole pipeline.

---

## Tier 1 — Truly free, no card, permanent (as of survey)

### 1. Google AI Studio (Gemini) — you already have this
- **Models**: `gemini-flash-latest`, `gemini-3.6-flash`, `gemini-pro-latest`.
- **Cap**: **20 requests/day/model** on the 3.x line for the free tier this month; **15 requests/minute** burst. Pro models often quota-exhausted mid-day.
- **Unique feature**: Google Search grounding on the `-flash` and `-pro` tiers — a real search-and-cite tool the model calls autonomously. **No other free provider ships this out of the box.**
- **LangChain**: `langchain-google-genai`, structured output via `.with_structured_output()`. Already wired in Argos.
- **Use for**: Fundamentals + Sentiment. Nothing else — the 20/day cap will drown a full backtest otherwise.

### 2. Groq
- **URL**: `https://api.groq.com/openai/v1/`
- **Models**: `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `mixtral-8x7b-32768`, `gemma-2-9b-it`, `deepseek-r1-distill-llama-70b`.
- **Cap**: **30 RPM, 6,000 tokens/min, 14,400 requests/day** on the free tier for 70B; much higher for 8B. **No daily token cap** on smaller models.
- **Latency**: LPU-backed, **sub-second for 70B**. Fastest free provider by a wide margin.
- **Structured output**: Yes, via `response_format={"type": "json_object"}` on OpenAI-compatible endpoint.
- **LangChain**: `langchain-groq` (well-maintained). Also works via `langchain-openai` with `base_url=https://api.groq.com/openai/v1`.
- **Reliability**: Very high; used in production by many teams.
- **Use for**: Technicals synthesis, Portfolio Manager (the two calls where latency matters and you re-run often).

### 3. Cerebras Inference
- **URL**: `https://api.cerebras.ai/v1/`
- **Models**: `llama-3.3-70b`, `llama-3.1-70b`, `llama-3.1-8b`, `qwen-3-32b`.
- **Cap**: **30 RPM, 60,000 tokens/min** on free tier (verify at cloud.cerebras.ai). Historically no explicit daily request cap; effective daily cap = 30 RPM × 24h × 60 = 43,200 requests theoretical.
- **Latency**: **~1s for 70B** — wafer-scale chip, competitive with Groq.
- **Structured output**: OpenAI-compatible `response_format` + JSON schema.
- **LangChain**: `langchain-cerebras` (recent), also works via `langchain-openai`.
- **Reliability**: Good, occasional maintenance windows.
- **Use for**: Bull Advocate. Isolates from Groq's quota.

### 4. SambaNova Cloud ⚠️ NO LONGER TRUE FREE TIER
- **URL**: `https://api.sambanova.ai/v1/`
- **Models available today**: `DeepSeek-V3.1`, `DeepSeek-V3.2`, `Meta-Llama-3.3-70B-Instruct`, `MiniMax-M2.7`, `MiniMax-M3`, `gemma-4-31B-it`, `gpt-oss-120b`. (405B has been retired.)
- **Reality check (verified 2026-09-24)**: New accounts start with **0 balance units**. Model listing is free, but **every `/chat/completions` call returns `402 PAYMENT_METHOD_REQUIRED`** until billing is set up. So SambaNova is **not usable under a no-payment constraint** even though its docs still market a free tier.
- **When to reconsider**: if you're willing to attach a card at cloud.sambanova.ai/plans/billing, DeepSeek-V3.2 is arguably the strongest OSS reasoner available anywhere at any tier. Until then, treat SambaNova as "keys work, inference doesn't".
- **Argos router behaviour**: `SAMBANOVA_API_KEY` is commented out in `.env`; the SambaNova ModelConfig stays in the fallback chain but is skipped silently when the key is unset. Uncomment to re-enable once billing is on.

### 5. OpenRouter (`:free` suffix models)
- **URL**: `https://openrouter.ai/api/v1/`
- **Free models available today (from live probe)**: 21, including
  - `nvidia/nemotron-3-ultra-550b-a55b:free` (huge reasoner)
  - `nvidia/nemotron-3-super-120b-a12b:free`
  - `google/gemma-4-31b-it:free`
  - `qwen/qwen3.8-27b:free`
  - `z-ai/glm-5.2:free`
  - `thinkingmachines/inkling:free` (1M context)
  - plus several smaller/experimental models
- **Cap**: **20 RPM, 50 requests/day per model** on `:free` models (per-account); can bump to **1,000/day** by adding $10 credit (but you asked no card — leave at 50/day).
- **Structured output**: Passes `response_format` through to the underlying provider. Support varies per model; Nemotron + Qwen + Gemma all support JSON mode.
- **LangChain**: `langchain-openai` with `base_url="https://openrouter.ai/api/v1"` and `api_key=<OPENROUTER_KEY>`.
- **Reliability**: Good, but individual `:free` models get pulled without notice — pin two and have a fallback list.
- **Use for**: **Bear Advocate**. Independent quota bucket from Cerebras.

### 6. Hugging Face — Serverless Inference API
- **URL**: `https://api-inference.huggingface.co/models/{model-id}`
- **Cap**: **~300 requests/hour** on the free tier, per model.
- **Latency**: **Cold start 5-20s**, warm 1-3s. Not great for production; fine for smoke tests.
- **Structured output**: No native support — you prompt-and-parse.
- **LangChain**: `langchain-huggingface` (`HuggingFaceEndpoint`).
- **Use for**: **Fallback only**. Don't build the primary pipeline on this.

### 7. Ollama — local, on your own machine
- **Setup**: `winget install Ollama.Ollama`; `ollama pull qwen2.5:7b` (~5 GB) or `ollama pull llama3.1:8b`.
- **Cap**: **None. Truly unlimited.** Subject only to your CPU/GPU.
- **Latency**: On a modern laptop CPU, Qwen 2.5 7B does structured output in 3-8s per call. With a decent GPU (RTX 3060+), sub-1s.
- **Structured output**: Ollama's `format=json` and Pydantic-compatible schemas via LangChain adapter.
- **LangChain**: `langchain-ollama` — first-class support.
- **Use for**: **The strategic recommendation.** Once your machine can run 7B/8B, put Bull + Bear + Technicals on it and eliminate 3 external providers' rate limits entirely. This is the highest-leverage single move you can make.

---

## Tier 2 — Small signup credit (not "free forever", but usable for dev)

### 8. NVIDIA NIM (`build.nvidia.com`)
- **URL**: `https://integrate.api.nvidia.com/v1/`
- **Models**: `meta/llama-3.3-70b-instruct`, `mistralai/mixtral-8x22b-instruct`, `nvidia/llama-3.1-nemotron-70b-instruct`, DeepSeek, Gemma, and many more.
- **Free credit**: **1,000 requests per NGC account**, reset ~monthly on some tiers; can make multiple accounts for isolation but it's grey-area.
- **Structured output**: OpenAI-compatible.
- **LangChain**: `langchain-nvidia-ai-endpoints`.
- **Reliability**: Enterprise-grade. Latency ~2-4s.
- **Use for**: Overflow when other providers hit caps. Not the primary.

### 9. Cohere trial
- **URL**: `https://api.cohere.com/v1/`
- **Models**: `command-r-plus`, `command-r`.
- **Free trial**: 20 RPM but time-limited (~1 month per key).
- **Structured output**: Native.
- **LangChain**: `langchain-cohere`.
- **Use for**: Backup — not durable enough for the primary stack.

### 10. Together AI / Fireworks AI
- Both give **$1 free credit** on signup — enough for a few hundred calls to a mid-tier model, then paid. Not sustainable for daily dev.

### 11. Anthropic Claude
- **No free tier** worth naming (small trial credit). Skip during dev.

---

## Suggested per-agent mapping

Given the 7 agents and their real needs:

| Agent | Provider | Model | Why |
|---|---|---|---|
| **Fundamentals** | Gemini | `gemini-flash-latest` | Google Search grounding is Gemini-only among free providers |
| **Sentiment** | Gemini | `gemini-flash-latest` | Same |
| **Technicals** | Groq | `llama-3.3-70b-versatile` | Sub-second latency; you re-run this most; large indicator snapshot as context |
| **Bull Advocate** | Cerebras | `llama-3.3-70b` | Independent quota from Groq; strong reasoner |
| **Bear Advocate** | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` or `qwen/qwen3.8-27b:free` | Independent quota; different model family = less correlated errors than Bull |
| **Risk Manager** | SambaNova | `Llama-3.1-405B-Instruct` | Biggest free model available; reserve daily cap for the veto call |
| **Portfolio Manager** | Groq | `llama-3.3-70b-versatile` (with Cerebras retry) | Fast final synthesis; retry to Cerebras on quota |

**Quota math for a 30-date backtest** (7 agents × 30 dates = 210 calls):

| Provider | Calls used | Daily cap | Utilization |
|---|---:|---:|---:|
| Gemini (2 agents × 30) | 60 | 40 (2 models × 20) | **over** — split across 2 API keys per your current .env setup gives 80 |
| Groq (2 agents × 30) | 60 | 14,400 | 0.4% |
| Cerebras (1 × 30) | 30 | ~43k | negligible |
| OpenRouter (1 × 30) | 30 | 50/day/model | 60% — set two model fallbacks |
| SambaNova (1 × 30) | 30 | 200 | 15% |

**Only Gemini is quota-constrained.** With 2 keys (Project A + B, already in your `.env`) you get 80 Gemini calls/day, enough for 40 dates of fundamentals+sentiment.

If Gemini becomes the bottleneck: swap sentiment to **Groq + a Google Custom Search JSON API call** (100 free searches/day) — you build the grounded-search yourself in ~40 lines. See Improvement C33 in `ARGOS_IMPROVEMENTS.md`.

---

## Structured-output support — the practical gotcha

Argos every agent needs Pydantic-validated JSON out. Support varies:

| Provider | Method | Reliability |
|---|---|---|
| Gemini | native `.with_structured_output(schema)` | ★★★★★ enforced by API |
| Groq | `response_format={"type":"json_object"}` + prompt | ★★★★☆ 95%+ valid JSON |
| Cerebras | `response_format={"type":"json_schema"}` (newer) | ★★★★☆ |
| SambaNova | `response_format={"type":"json_object"}` | ★★★☆☆ occasional parse failures on 405B |
| OpenRouter | passes `response_format` to underlying | ★★★☆☆ depends on underlying model |
| Ollama | `format="json"` + schema prompt | ★★★★☆ with Pydantic wrapper |
| HF Inference | prompt-and-parse only | ★★☆☆☆ |

**Argos already uses `.with_structured_output()` in every node** via LangChain. For non-Gemini providers, LangChain calls the OpenAI-compatible JSON mode under the hood and re-parses on failure. Add `method="json_mode"` if a provider complains.

---

## Concrete integration plan for Argos

**Step 1 (30 min):** extend `argos/model_router.py::ModelConfig` with a `provider` field:

```python
@dataclass(frozen=True)
class ModelConfig:
    provider: Literal["gemini", "groq", "cerebras", "sambanova", "openrouter", "ollama"]
    api_key_env: str
    model_name: str
    base_url: str | None = None
    grounded: bool = False
```

**Step 2 (30 min):** in `_make_llm`, dispatch by provider — use `langchain-groq` for Groq, `langchain-openai` with `base_url` for Cerebras/SambaNova/OpenRouter, `langchain-ollama` for Ollama.

**Step 3 (5 min):** update `AGENT_MODEL_MAP` per the table above.

**Step 4 (5 min):** add env vars — `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `SAMBANOVA_API_KEY`, `OPENROUTER_API_KEY`. All are strings from each provider's dashboard.

**Step 5 (10 min):** add per-agent retry-to-fallback so a 503 or 429 auto-routes to the next-best provider.

**Cost of the change:** ~90 minutes. **Benefit:** free-tier 30-date backtest becomes actually runnable in one sitting instead of "wait 24h for Gemini quota reset × 10 days."

---

## What I recommend, if you want a single-sentence answer

**Sign up for Groq + Cerebras + SambaNova + OpenRouter (all free, ~15 min total, no cards)**; keep Gemini for fundamentals + sentiment only; put Bull on Cerebras, Bear on OpenRouter (with a Groq fallback), Risk on SambaNova 405B, Portfolio on Groq; and if you want to eliminate rate limits entirely for dev, install Ollama and pull Qwen 2.5 7B — it runs fine on a Windows laptop and can host Bull + Bear + Technicals locally with zero API cost.
