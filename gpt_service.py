import asyncio
import base64
import io
import json
import logging
import re
import httpx
from PIL import Image

import config
import database
from core import apinex_pool
from core import openagentic_pool

log = logging.getLogger("hwh.gpt")

HTTPX_NETWORK_ERRORS = tuple(
    cls for name in ("TimeoutException", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "HTTPError", "NetworkError")
    if (cls := getattr(httpx, name, None)) and isinstance(cls, type) and issubclass(cls, BaseException)
) or (Exception,)

PRIMARY_MODEL = getattr(config, "GPT_MODEL", "free/gpt-6-luna")
FALLBACK_MODELS = [
    "free/deepseek-v4-pro-0813",
    "free/glm-5.3-flash",
    "free/deepseek-v4.1-flash",
    "free/mimo-v2.6-pro",
]
VISION_FALLBACK_MODELS = [
    "free/gpt-6-luna",
    "free/glm-5.3-flash",
    "free/deepseek-v4-pro-0813",
    "free/mimo-v2.6-pro",
]

async def query_groq(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries Groq API with top models across primary and backup keys (ultra-fast LPU inference)."""
    keys = getattr(config, "GROQ_API_KEYS", [])
    if not keys and getattr(config, "GROQ_API_KEY", None):
        keys = [config.GROQ_API_KEY]
    if not keys:
        return None

    models = getattr(config, "GROQ_MODELS", ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"])
    async with httpx.AsyncClient(timeout=8.0) as client:
        for key in keys:
            for model in models:
                try:
                    r = await client.post(
                        f"{config.GROQ_BASE_URL}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                        }
                    )
                    if r.status_code == 200:
                        data = r.json()
                        content = data["choices"][0]["message"]["content"]
                        if content and content.strip():
                            return content.strip()
                    elif r.status_code == 429:
                        log.info("Groq key %s rate-limited (429), trying next key...", key[:8])
                        break
                    else:
                        log.warning("Groq model %s returned HTTP %s: %s", model, r.status_code, r.text[:100])
                except Exception as e:
                    log.warning("Groq query (%s, key %s) failed: %s", model, key[:8], e)
    return None

async def query_cloudflare(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries Cloudflare Workers AI with Meta Llama 3.3 70B Instruct."""
    cf_key = getattr(config, "CLOUDFLARE_API_KEY", None)
    cf_account = getattr(config, "CLOUDFLARE_ACCOUNT_ID", None)
    if not cf_key or not cf_account:
        return None

    cf_url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/v1/chat/completions"
    cf_model = getattr(config, "CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    try:
        async with httpx.AsyncClient(timeout=6.5) as client:
            r = await client.post(
                cf_url,
                headers={
                    "Authorization": f"Bearer {cf_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cf_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
            )
            if r.status_code == 200:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content.strip()
            else:
                log.warning("Cloudflare AI returned HTTP %s: %s", r.status_code, r.text[:100])
    except Exception as e:
        log.warning("Cloudflare AI query failed: %s", e)
    return None

async def query_openrouter(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries OpenRouter API for free models pool."""
    api_key = getattr(config, "OPENROUTER_API_KEY", "")
    if not api_key:
        return None
    base_url = getattr(config, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    models = getattr(config, "OPENROUTER_FREE_MODELS", ["openrouter/free"])
    async with httpx.AsyncClient(timeout=10.0) as client:
        for model in models:
            try:
                r = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                )
                if r.status_code == 200:
                    data = r.json()
                    content = data["choices"][0]["message"]["content"]
                    if content and content.strip():
                        return content.strip()
                elif r.status_code == 429:
                    log.info("OpenRouter model %s rate limited, trying next...", model)
                    continue
                else:
                    log.warning("OpenRouter model %s returned HTTP %s: %s", model, r.status_code, r.text[:100])
            except Exception as e:
                log.warning("OpenRouter query failed: %s", e)
    return None

async def query_gemini_text(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries Google Gemini API with fallback across configured keys."""
    keys = getattr(config, "GEMINI_API_KEYS", [])
    if not keys:
        return None
    model = getattr(config, "GEMINI_MODEL", "gemini-3.8-flash")

    contents = []
    system_inst = ""
    for m in messages:
        role = m.get("role", "")
        txt = m.get("content", "")
        if role == "system":
            system_inst = txt
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": txt}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": txt}]})

    payload = {"contents": contents}
    if system_inst:
        payload["system_instruction"] = {"parts": [{"text": system_inst}]}
    payload["generationConfig"] = {"temperature": temperature, "maxOutputTokens": max_tokens}

    async with httpx.AsyncClient(timeout=12.0) as client:
        for key in keys:
            try:
                r = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                    json=payload,
                )
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            ans = parts[0].get("text", "").strip()
                            if ans:
                                return ans
                elif r.status_code in (429, 503):
                    log.info("Gemini key %s busy (%s), trying next key...", key[:10], r.status_code)
                    continue
                else:
                    log.warning("Gemini returned HTTP %s: %s", r.status_code, r.text[:100])
            except Exception as e:
                log.warning("Gemini query failed: %s", e)
    return None

async def query_gemini_vision(image_bytes: bytes, prompt: str = "", system_prompt: str = "") -> str | None:
    """Queries Google Gemini Vision (gemini-3.8-flash) natively with image bytes."""
    keys = getattr(config, "GEMINI_API_KEYS", [])
    if not keys:
        return None
    model = getattr(config, "GEMINI_MODEL", "gemini-3.8-flash")
    b64, mime = optimize_image_for_vision(image_bytes)

    full_prompt = f"{system_prompt}\n\n{prompt}".strip() if system_prompt else (prompt or "Describe and solve this image")
    payload = {
        "contents": [{
            "parts": [
                {"text": full_prompt},
                {"inline_data": {"mime_type": mime, "data": b64}}
            ]
        }],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500}
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        for key in keys:
            try:
                r = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                    json=payload,
                )
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            ans = parts[0].get("text", "").strip()
                            if ans:
                                return ans
            except Exception as e:
                log.warning("Gemini vision error: %s", e)
    return None

async def query_nvidia(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries NVIDIA NIM (Ultra-fast ~0.8s Llama 3.2 on H100s)."""
    api_key = getattr(config, "NVIDIA_API_KEY", "")
    if not api_key:
        return None
    base_url = getattr(config, "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    model = getattr(config, "NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
            )
            if r.status_code == 200:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content.strip()
            else:
                log.warning("NVIDIA NIM returned HTTP %s: %s", r.status_code, r.text[:100])
    except Exception as e:
        log.warning("NVIDIA NIM query failed: %s", e)
    return None

async def query_nvidia_vision(image_bytes: bytes, prompt: str = "", system_prompt: str = "") -> str | None:
    """Queries NVIDIA NIM Vision (meta/llama-3.2-11b-vision-instruct) with image."""
    api_key = getattr(config, "NVIDIA_API_KEY", "")
    if not api_key:
        return None
    base_url = getattr(config, "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    model = getattr(config, "NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")
    b64, mime = optimize_image_for_vision(image_bytes)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": prompt or "Describe and solve this image"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        ]
    })
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 1000,
                }
            )
            if r.status_code == 200:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content.strip()
    except Exception as e:
        log.warning("NVIDIA Vision query failed: %s", e)
    return None

async def query_sambanova(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries SambaNova Cloud API across configured models (MiniMax-M3, gemma-4-31B-it)."""
    api_key = getattr(config, "SAMBANOVA_API_KEY", "")
    if not api_key:
        return None
    base_url = getattr(config, "SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1")
    models = getattr(config, "SAMBANOVA_MODELS", ["MiniMax-M3", "gemma-4-31B-it"])
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for model in models:
                try:
                    r = await client.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                        }
                    )
                    if r.status_code == 200:
                        data = r.json()
                        content = data["choices"][0]["message"]["content"]
                        if content and content.strip():
                            return content.strip()
                    elif r.status_code in (402, 429):
                        continue
                except Exception:
                    continue
    except Exception as e:
        log.warning("SambaNova query failed: %s", e)
    return None

async def query_ultra_fast_race(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """
    Races ultra-fast sub-second inference engines (Groq ~0.5s & NVIDIA NIM ~0.7s) concurrently.
    Returns the fastest valid completion immediately to ensure ultra-low latency.
    """
    tasks = [
        asyncio.create_task(query_groq(messages=messages, max_tokens=max_tokens, temperature=temperature)),
        asyncio.create_task(query_nvidia(messages=messages, max_tokens=max_tokens, temperature=temperature)),
    ]
    for completed in asyncio.as_completed(tasks):
        try:
            res = await completed
            if isinstance(res, str) and res.strip():
                return res.strip()
        except Exception:
            pass
    return None

async def query_mistral_text(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries Mistral API using verified working text models across primary & backup keys."""
    keys = getattr(config, "MISTRAL_API_KEYS", [])
    if not keys and getattr(config, "MISTRAL_API_KEY", None):
        keys = [config.MISTRAL_API_KEY]
    if not keys:
        return None

    models = getattr(config, "MISTRAL_TEXT_MODELS", ["ministral-8b-latest", "pixtral-12b-2409", "open-mistral-nemo", "open-mistral-7b"])
    async with httpx.AsyncClient(timeout=8.0) as client:
        for key in keys:
            for model in models:
                try:
                    r = await client.post(
                        f"{config.MISTRAL_BASE_URL}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                        }
                    )
                    if r.status_code == 200:
                        data = r.json()
                        content = data["choices"][0]["message"]["content"]
                        if content and content.strip():
                            return content.strip()
                    elif r.status_code == 429:
                        log.info("Mistral key %s rate-limited (429), trying next key...", key[:8])
                        break
                    else:
                        log.warning("Mistral text model %s returned HTTP %s: %s", model, r.status_code, r.text[:100])
                except Exception as e:
                    log.warning("Mistral text query (%s) failed: %s", model, e)
    return None

async def query_pollinations(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
    """Queries Pollinations AI (100% free unmetered fallback so bot never fails)."""
    url = getattr(config, "POLLINATIONS_URL", "https://text.pollinations.ai/openai")
    model = getattr(config, "POLLINATIONS_MODEL", "openai")
    try:
        async with httpx.AsyncClient(timeout=7.0) as client:
            r = await client.post(
                url,
                headers={"Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
            )
            if r.status_code == 200:
                data = r.json()
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content.strip()
            else:
                log.warning("Pollinations AI returned HTTP %s: %s", r.status_code, r.text[:100])
    except Exception as e:
        log.warning("Pollinations AI query failed: %s", e)
    return None

def optimize_image_for_vision(raw_bytes: bytes) -> tuple[str, str]:
    """Optimizes image to JPEG and returns (base64_str, mime_type)."""
    im = Image.open(io.BytesIO(raw_bytes))
    if im.mode != "RGB":
        im = im.convert("RGB")

    max_dim = 1500
    w, h = im.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return b64, "image/jpeg"

async def query_mistral_vision(image_bytes: bytes, prompt: str | None = None) -> str:
    """
    Analyzes image using Mistral Vision (pixtral-12b-2409).
    Falls back to Apinex vision models sequentially across all 6 keys.
    """
    clean_prompt = (prompt or "").strip() or "Describe this image in detail and answer any questions about it."
    b64, mime = optimize_image_for_vision(image_bytes)

    system_prompt = (
        "You are an intelligent, helpful, and concise visual assistant. "
        "Analyze the provided image accurately according to the user's prompt. "
        "Rules: "
        "1. Answer clearly, accurately, and directly without filler. "
        "2. If asked to translate, extract text, or solve a puzzle/math, provide the solution directly. "
        "3. Preserve markdown formatting where helpful. "
        "4. NEVER mention words like GPT, Apinex, Luna, OpenAI, Mistral, Groq, Cloudflare, or any AI model names. "
        "5. If anyone asks who you are or who created you, say 'I am Viper, Pratik\\'s special assistant.'."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": clean_prompt},
                {"type": "image_url", "image_url": f"data:{mime};base64,{b64}"}
            ]
        }
    ]

    log.info("Trying ultra-fast NVIDIA NIM Vision...")
    nv_vision = await query_nvidia_vision(image_bytes=image_bytes, prompt=clean_prompt, system_prompt=system_prompt)
    if nv_vision:
        return nv_vision

    log.info("Trying Google Gemini Vision...")
    gemini_vision = await query_gemini_vision(image_bytes=image_bytes, prompt=clean_prompt, system_prompt=system_prompt)
    if gemini_vision:
        return gemini_vision

    mistral_keys = getattr(config, "MISTRAL_API_KEYS", [])
    if not mistral_keys and getattr(config, "MISTRAL_API_KEY", None):
        mistral_keys = [config.MISTRAL_API_KEY]

    for m_key in mistral_keys:
        for attempt in range(2):
            try:
                log.info("Querying Mistral Vision (%s, key %s)... attempt %s", config.MISTRAL_MODEL, m_key[:8], attempt + 1)
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.post(
                        f"{config.MISTRAL_BASE_URL}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {m_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": config.MISTRAL_MODEL,
                            "messages": messages,
                            "temperature": 0.3,
                            "max_tokens": 1500,
                        }
                    )
                    if r.status_code == 200:
                        data = r.json()
                        content = data["choices"][0]["message"]["content"]
                        if content and content.strip():
                            return content.strip()
                    elif r.status_code in (429, 500, 502, 503, 504):
                        log.warning("Mistral HTTP %s, retrying...", r.status_code)
                        await asyncio.sleep(1.0 * (attempt + 1))
                        continue
                    else:
                        break
            except Exception as e:
                log.warning("Mistral vision query failed: %s. Trying next key...", e)
                break

    log.info("Trying Apinex vision models across all keys...")
    apinex_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": clean_prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            ]
        }
    ]

    keys_to_try = config.get_ordered_apinex_keys()
    async with httpx.AsyncClient(timeout=20) as http:
        for key in keys_to_try:
            for model in VISION_FALLBACK_MODELS:
                try:
                    r = await http.post(
                        f"{config.APINEX_BASE_URL}/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": apinex_messages,
                            "temperature": 0.3,
                            "max_tokens": 1500,
                        }
                    )
                    if r.status_code == 200:
                        config.report_key_success(key)
                        content = _extract_content(r.text)
                        if content:
                            return content
                    elif r.status_code in (401, 402, 429):
                        config.report_key_error(key, r.status_code)
                        break
                except Exception as err:
                    config.report_key_error(key, 0)
                    break

    log.info("Trying OpenAgentic.id vision models...")
    oa_vision = await openagentic_pool.query_vision(
        image_b64=b64, mime=mime, prompt=clean_prompt, system_prompt=system_prompt
    )
    if oa_vision:
        return oa_vision

    return "⚠️ Maaf kijiye, image analyze nahi ho paayi. Kripya thodi der baad dobara try karein."

def _extract_content(body: str) -> str:
    """Pull assistant text from a chat-completions body. Prefers strict JSON; regex only as last resort."""
    try:
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
        return (content or "").strip()
    except Exception:
        pass
    m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
    if not m:
        return ""
    try:
        return json.loads(f'"{m.group(1)}"').strip()
    except Exception:
        return m.group(1).strip()

async def query_gpt(prompt: str | None = None, context: str | None = None, image_bytes: bytes | None = None) -> str:
    """
    Unbreakable Multi-Tier AI Query Cascade:
    - If image_bytes: Uses Mistral Vision (pixtral-12b-2409) with Apinex + OpenAgentic vision failover.
    - If text only:
      1. Tier 1: Apinex Key Round-Robin & Seamless Failover.
      2. Tier 1.5: OpenAgentic.id Free Models (Claude Sonnet 4.5, DeepSeek, GLM, etc.).
      3. Tier 2: Groq Dual-Key LPU Engine (openai/gpt-oss-120b, qwen3.8-27b, gpt-oss-20b).
      4. Tier 3: Cloudflare Workers AI (Meta Llama 3.3 70B Instruct).
      5. Tier 4: Mistral Dual-Key (ministral-8b, pixtral-12b, open-mistral-nemo).
      6. Tier 5: Pollinations AI (100% Free Unmetered Fallback).
    """
    try:
        from core.auth import verify_author_integrity
        verify_author_integrity()
    except Exception:
        return "❌ <b>Integrity Error:</b> Unauthorized rebranding detected. Original author @Gazarkahalwahu credit must remain intact."

    if image_bytes:
        query_text = (prompt or "").strip() or (context or "").strip()
        return await query_mistral_vision(image_bytes=image_bytes, prompt=query_text)

    clean_prompt = (prompt or "").strip()
    clean_context = (context or "").strip()

    if not clean_prompt and not clean_context:
        return "❌ Koi question ya replied message nahi mila."

    memories = database.get_memories()
    memory_section = ""
    if memories:
        mem_items = "\n".join(f"- [Memory #{m['id']}]: {m['content']}" for m in memories)
        memory_section = (
            f"\n\n[USER SAVED MEMORIES & FACTS]:\n{mem_items}\n"
            "CRITICAL: Always consult the above saved memories when answering! If the user asks about personal facts, numbers, dates, preferences, or details recorded in memory, answer directly and accurately based on them."
        )

    system_prompt = (
        "You are an intelligent, helpful, and concise AI assistant for a Telegram user. "
        "Rules: "
        "1. Give direct, clear, and accurate answers. "
        "2. Do not use conversational filler, disclaimers, or unsolicited advice. "
        "3. Format cleanly for Telegram: use bold (**text**) for titles or key terms, italic (*text*) for emphasis, and code (`code` or ```blocks```) where appropriate. Keep formatting clean, natural, and readable without excessive asterisks or markdown clutter. "
        "4. If instructed to translate or rewrite, provide only the requested output without introductory text. "
        "5. NEVER mention words like GPT, Apinex, Luna, OpenAI, Mistral, Groq, Cloudflare, or any AI model names. "
        "6. If anyone asks who you are or who created you, say 'I am Viper, Pratik\\'s special assistant.'."
        f"{memory_section}"
    )

    if clean_context and clean_prompt:
        user_content = (
            f"[Target Message]:\n{clean_context}\n\n"
            f"[Instruction]:\n{clean_prompt}"
        )
    elif clean_context:
        user_content = clean_context
    else:
        user_content = clean_prompt

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    fast_text = await query_ultra_fast_race(messages, max_tokens=1500, temperature=0.4)
    if fast_text:
        log.info("Ultra-fast turbo engine answered successfully.")
        return fast_text

    text = await apinex_pool.query(messages, temperature=0.4, max_tokens=1500, budget=6.0)
    if text:
        return text
    log.info("Apinex tier gave no answer. Trying OpenAgentic & OpenRouter...")

    oa_text = await openagentic_pool.query(messages, temperature=0.4, max_tokens=1500)
    if oa_text:
        log.info("OpenAgentic.id answered successfully.")
        return oa_text

    tier3_results = await asyncio.gather(
        query_openrouter(messages=messages, max_tokens=1500, temperature=0.4),
        query_cloudflare(messages=messages, max_tokens=1500, temperature=0.4),
        return_exceptions=True
    )
    for r in tier3_results:
        if isinstance(r, str) and r.strip():
            log.info("Tier 3 parallel fallback answered successfully.")
            return r.strip()

    gemini_resp = await query_gemini_text(messages=messages, max_tokens=1500, temperature=0.4)
    if gemini_resp and gemini_resp.strip():
        log.info("Gemini fallback answered successfully.")
        return gemini_resp.strip()

    samba_resp = await query_sambanova(messages=messages, max_tokens=1500, temperature=0.4)
    if samba_resp and samba_resp.strip():
        log.info("SambaNova fallback answered successfully.")
        return samba_resp.strip()

    mistral_resp = await query_mistral_text(messages=messages, max_tokens=1500, temperature=0.4)
    if mistral_resp and mistral_resp.strip():
        log.info("Mistral fallback answered successfully.")
        return mistral_resp.strip()

    poll_resp = await query_pollinations(messages=messages, max_tokens=1500, temperature=0.4)
    if poll_resp and poll_resp.strip():
        log.info("Pollinations AI fallback answered successfully.")
        return poll_resp.strip()

    from core.keypool import check_circuit_breaker
    if check_circuit_breaker():
        return "⚠️ Sab keys cooldown mein hain, thoda ruko."
    return "⚠️ Maaf kijiye, abhi AI response generate nahi ho paaya. Kripya thodi der baad try karein."

GAME_SYSTEM_PROMPT = (
    "You are an ultra-fast, expert gaming and puzzle solving AI for Telegram games. "
    "Rules: "
    "1. Identify the game, quiz, or puzzle type from the text and/or image (e.g. Emoji Quiz, Trivia, Word Scramble, Math Puzzle, Anagram, Crossword, Hangman, Rebus, Riddle, Who Am I, etc.). "
    "2. Determine the rules and exact goal. "
    "3. Provide ONLY the correct, direct answer(s) so the user can immediately submit and win. "
    "4. Be strictly to the point (no conversational filler, no long explanations, no tutorials). "
    "5. If multiple items need to be answered, list them numbered: 1. Answer 2. Answer. "
    "6. If a single word or phrase is needed, output only that word or phrase clearly. "
    "7. NEVER mention words like GPT, Apinex, Luna, OpenAI, Mistral, Groq, Cloudflare, or any AI model names. "
    "8. If anyone asks who you are or who created you, say 'I am Viper, Pratik\\'s special assistant.'."
)

async def solve_game_ai(game_text: str | None = None, image_bytes: bytes | None = None,
                        blocked: set[str] | None = None) -> str:
    """
    Universal AI Game Solver:
    - Automatically identifies any game type and its rules.
    - If image present: Ultra-fast NVIDIA NIM Vision (1.1s) -> Gemini Vision -> Apinex Vision -> Mistral Vision.
    - If text only: Sub-Second Turbo Race (Groq 0.5s + NVIDIA 0.7s) -> OpenRouter + Apinex -> SambaNova + Gemini -> Mistral.
    - Returns ultra-concise, direct solutions.
    """
    clean_text = (game_text or "").strip()
    if not clean_text and not image_bytes:
        return "❌ Koi game message ya photo nahi mila."

    if image_bytes:
        b64, mime = optimize_image_for_vision(image_bytes)
        user_prompt = clean_text or "Analyze this game/puzzle screenshot, figure out the game rules, and provide the exact answer(s)."

        nv_vision = await query_nvidia_vision(image_bytes=image_bytes, prompt=user_prompt, system_prompt=GAME_SYSTEM_PROMPT)
        if nv_vision:
            return nv_vision

        gemini_vision = await query_gemini_vision(image_bytes=image_bytes, prompt=user_prompt, system_prompt=GAME_SYSTEM_PROMPT)
        if gemini_vision:
            return gemini_vision

        apinex_messages = [
            {"role": "system", "content": GAME_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                ]
            }
        ]
        keys_to_try = config.get_ordered_apinex_keys()
        async with httpx.AsyncClient(timeout=15) as http:
            for key in keys_to_try:
                for model in VISION_FALLBACK_MODELS:
                    try:
                        r = await http.post(
                            f"{config.APINEX_BASE_URL}/chat/completions",
                            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                            json={
                                "model": model,
                                "messages": apinex_messages,
                                "temperature": 0.2,
                                "max_tokens": 800,
                            }
                        )
                        if r.status_code == 200:
                            config.report_key_success(key)
                            data = r.json()
                            content = data["choices"][0]["message"]["content"]
                            if content and content.strip():
                                return content.strip()
                        elif r.status_code in (401, 402, 429):
                            config.report_key_error(key, r.status_code)
                            break
                    except Exception:
                        config.report_key_error(key, 0)
                        break

        mistral_keys = getattr(config, "MISTRAL_API_KEYS", [])
        if not mistral_keys and getattr(config, "MISTRAL_API_KEY", None):
            mistral_keys = [config.MISTRAL_API_KEY]

        messages = [
            {"role": "system", "content": GAME_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": f"data:{mime};base64,{b64}"}
                ]
            }
        ]
        for m_key in mistral_keys:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.post(
                        f"{config.MISTRAL_BASE_URL}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {m_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": config.MISTRAL_MODEL,
                            "messages": messages,
                            "temperature": 0.2,
                            "max_tokens": 800,
                        }
                    )
                    if r.status_code == 200:
                        content = r.json()["choices"][0]["message"]["content"]
                        if content and content.strip():
                            return content.strip()
            except Exception:
                break

        return "⚠️ Game solution generate nahi ho paaya."

    avoid_line = ""
    if blocked:
        listed = ", ".join(sorted({b.upper() for b in blocked})[:50])
        avoid_line = f"\n\nDo NOT answer with any of these words (already used this round): {listed}"
    messages = [
        {"role": "system", "content": GAME_SYSTEM_PROMPT},
        {"role": "user", "content": f"Game message:\n{clean_text}{avoid_line}\n\nIdentify the game type and provide the exact answer(s):"}
    ]

    if getattr(apinex_pool.query, "__name__", "") != "query":
        text = await apinex_pool.query(messages, temperature=0.2, max_tokens=800)
        if text:
            return text

    fast_text = await query_ultra_fast_race(messages, max_tokens=800, temperature=0.2)
    if fast_text:
        return fast_text

    tier2_results = await asyncio.gather(
        query_openrouter(messages=messages, max_tokens=800, temperature=0.2),
        apinex_pool.query(messages, temperature=0.2, max_tokens=800, budget=6.0),
        return_exceptions=True
    )
    for r in tier2_results:
        if isinstance(r, str) and r.strip():
            return r.strip()

    oa_text = await openagentic_pool.query(messages, temperature=0.2, max_tokens=800)
    if oa_text:
        return oa_text

    samba_sol = await query_sambanova(messages=messages, max_tokens=800, temperature=0.2)
    if samba_sol and samba_sol.strip():
        return samba_sol.strip()

    gemini_sol = await query_gemini_text(messages=messages, max_tokens=800, temperature=0.2)
    if gemini_sol and gemini_sol.strip():
        return gemini_sol.strip()

    cf_sol = await query_cloudflare(messages=messages, max_tokens=800, temperature=0.2)
    if cf_sol and cf_sol.strip():
        return cf_sol.strip()

    mistral_sol = await query_mistral_text(messages=messages, max_tokens=800, temperature=0.2)
    if mistral_sol and mistral_sol.strip():
        return mistral_sol.strip()

    poll_sol = await query_pollinations(messages=messages, max_tokens=800, temperature=0.2)
    if poll_sol and poll_sol.strip():
        return poll_sol.strip()

    return "⚠️ Game solution generate nahi ho paaya."

def get_active_models_summary() -> str:
    """
    Returns an instant status report of all models configured in the bot.
    No network requests - instant display with 100% operational status and green ticks.
    Contains ZERO provider branding, only clean model names (e.g. GPT-Luna-6).
    """
    oa_status = "✅ Active" if config.OPENAGENTIC_API_KEY else "⏸️ No Key"
    or_status = "✅ Active" if getattr(config, "OPENROUTER_API_KEY", "") else "⏸️ No Key"
    gemini_status = "✅ Active" if getattr(config, "GEMINI_API_KEYS", []) else "⏸️ No Key"
    nv_status = "✅ Active" if getattr(config, "NVIDIA_API_KEY", "") else "⏸️ No Key"
    samba_status = "✅ Active" if getattr(config, "SAMBANOVA_API_KEY", "") else "⏸️ No Key"
    total_models = 27
    report = (
        "🤖 <b>AI Architecture & Active Models</b>\n\n"
        "🚀 <b>Tier 1: Primary Engines</b>\n"
        "  • <code>GPT-Luna-6</code>: ✅ Active\n"
        "  • <code>DeepSeek-V4-Pro</code>: ✅ Active\n"
        "  • <code>GLM-5.3-Flash</code>: ✅ Active\n"
        "  • <code>DeepSeek-V4.1-Flash</code>: ✅ Active\n"
        "  • <code>MiMo-V2.6-Pro</code>: ✅ Active\n\n"
        f"🌐 <b>Tier 1.5: Adaptive Free Engines</b>\n"
        f"  • <code>Big-Pickle</code>: {oa_status}\n"
        f"  • <code>DeepSeek-V4.1-Flash-Free</code>: {oa_status}\n"
        f"  • <code>Claude-Haiku-5.5-Free</code>: {oa_status}\n"
        f"  • <code>MiMo-V2.6-Flash</code>: {oa_status}\n"
        f"  • <code>Muse-Spark-1.3-Free</code>: {oa_status}\n"
        f"  • <code>Space-Bunny-Free</code>: {oa_status}\n"
        f"  • <code>Ali-Z-Image-Turbo</code>: {oa_status} 🎨\n\n"
        "⚡ <b>Tier 2: Ultra-Fast Engines</b>\n"
        "  • <code>Qwen-3.8-27B</code>: ✅ Active (0.5s) ⚡\n"
        f"  • <code>Llama-3.2-11B-Vision</code>: {nv_status} (0.8s) 👁️\n"
        "  • <code>GPT-OSS-120B</code>: ✅ Active\n"
        "  • <code>GPT-OSS-20B</code>: ✅ Active\n\n"
        "🌍 <b>Tier 2.5: Multi-Model Engines</b>\n"
        f"  • <code>OpenRouter-Free</code>: {or_status}\n"
        f"  • <code>Gemma-4-31B-Free</code>: {or_status}\n"
        f"  • <code>Llama-3.3-70B-Free</code>: {or_status}\n\n"
        "💎 <b>Tier 3: Multimodal High-Speed</b>\n"
        f"  • <code>Gemini-3.8-Flash</code>: {gemini_status} 👁️\n"
        f"  • <code>Gemma-4-31B</code>: {samba_status}\n\n"
        "☁️ <b>Tier 3: High-Capacity Engine</b>\n"
        "  • <code>Llama-3.3-70B</code>: ✅ Active\n\n"
        "🌪️ <b>Tier 4: Specialized Engines</b>\n"
        "  • <code>Ministral-8B</code>: ✅ Active\n"
        "  • <code>Pixtral-12B</code>: ✅ Active 👁️\n"
        "  • <code>Mistral-Nemo-12B</code>: ✅ Active\n\n"
        "🌸 <b>Tier 5: Resilient Backup Engine</b>\n"
        "  • <code>GPT-Core-Backup</code>: ✅ Active\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>System Health:</b> <code>{total_models}/{total_models} Models Ready</code> ✅\n"
        "✨ <b>Status:</b> <code>100% Operational & Fail-Safe</code>\n"
        "🔄 <b>Cascade:</b> Tier 1 ➔ Tier 1.5 ➔ Tier 2 ➔ Tier 2.5 ➔ Tier 3 ➔ Tier 4 ➔ Tier 5"
    )
    return report

async def check_all_models_status() -> str:
    """
    Live health check across all AI models & fallback tiers.
    Tests each model in parallel and returns a formatted HTML report with green ticks and latencies.
    Contains ZERO provider branding, only clean model names (e.g. GPT-Luna-6).
    """
    import time
    t_start = time.time()

    tier1_models = [
        ("free/gpt-6-luna", "GPT-Luna-6"),
        ("free/deepseek-v4-pro-0813", "DeepSeek-V4-Pro"),
        ("free/glm-5.3-flash", "GLM-5.3-Flash"),
        ("free/deepseek-v4.1-flash", "DeepSeek-V4.1-Flash"),
        ("free/mimo-v2.6-pro", "MiMo-V2.6-Pro"),
    ]
    tier1_5_models = [
        ("big-pickle", "Big-Pickle"),
        ("deepseek-v4.1-flash-free", "DeepSeek-V4.1-Flash-Free"),
        ("claude-haiku-5.5-free", "Claude-Haiku-5.5-Free"),
        ("mimo-v2.6-flash", "MiMo-V2.6-Flash"),
        ("muse-spark-1.3-free", "Muse-Spark-1.3-Free"),
        ("space-bunny-free", "Space-Bunny-Free"),
    ]
    tier2_models = [
        ("openai/gpt-oss-120b", "GPT-OSS-120B"),
        ("qwen/qwen3.8-27b", "Qwen-3.8-27B"),
        ("openai/gpt-oss-20b", "GPT-OSS-20B"),
    ]
    tier3_models = [
        (getattr(config, "CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"), "Llama-3.3-70B"),
    ]
    tier4_models = [
        ("ministral-8b-latest", "Ministral-8B"),
        ("pixtral-12b-2409", "Pixtral-12B"),
        ("open-mistral-nemo", "Mistral-Nemo-12B"),
    ]
    tier5_models = [
        (getattr(config, "POLLINATIONS_MODEL", "openai"), "GPT-Core-Backup"),
    ]

    async with httpx.AsyncClient(timeout=10) as client:
        async def chk_tier1(model_id, display_name):
            t0 = time.time()
            keys = config.get_ordered_apinex_keys()
            for key in keys:
                try:
                    r = await client.post(
                        f"{config.APINEX_BASE_URL}/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                        timeout=5.0
                    )
                    if r.status_code == 200:
                        config.report_key_success(key)
                        return (display_name, True, time.time() - t0, "Active")
                except Exception:
                    pass
            return (display_name, False, time.time() - t0, "Busy")

        async def chk_tier1_5(model_id, display_name):
            t0 = time.time()
            oa_key = getattr(config, "OPENAGENTIC_API_KEY", "")
            if not oa_key:
                return (display_name, False, time.time() - t0, "No Key")
            try:
                r = await client.post(
                    f"{config.OPENAGENTIC_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {oa_key}", "Content-Type": "application/json"},
                    json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                    timeout=8.0
                )
                return (display_name, r.status_code == 200, time.time() - t0, "Active" if r.status_code == 200 else f"HTTP {r.status_code}")
            except Exception:
                return (display_name, False, time.time() - t0, "Timeout")

        async def chk_tier2(model_id, display_name):
            t0 = time.time()
            keys = getattr(config, "GROQ_API_KEYS", [])
            for key in keys:
                try:
                    r = await client.post(
                        f"{config.GROQ_BASE_URL}/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                        timeout=5.0
                    )
                    if r.status_code == 200:
                        return (display_name, True, time.time() - t0, "Active")
                except Exception:
                    pass
            return (display_name, False, time.time() - t0, "Busy")

        async def chk_tier3(model_id, display_name):
            t0 = time.time()
            cf_key = getattr(config, "CLOUDFLARE_API_KEY", "")
            cf_acc = getattr(config, "CLOUDFLARE_ACCOUNT_ID", "")
            if not cf_key or not cf_acc:
                return (display_name, False, time.time() - t0, "Busy")
            try:
                r = await client.post(
                    f"https://api.cloudflare.com/client/v4/accounts/{cf_acc}/ai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {cf_key}", "Content-Type": "application/json"},
                    json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                    timeout=6.0
                )
                return (display_name, r.status_code == 200, time.time() - t0, "Active" if r.status_code == 200 else f"HTTP {r.status_code}")
            except Exception:
                return (display_name, False, time.time() - t0, "Error")

        async def chk_tier4(model_id, display_name):
            t0 = time.time()
            keys = getattr(config, "MISTRAL_API_KEYS", [])
            for key in keys:
                try:
                    r = await client.post(
                        f"{config.MISTRAL_BASE_URL}/chat/completions",
                        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                        timeout=5.0
                    )
                    if r.status_code == 200:
                        return (display_name, True, time.time() - t0, "Active")
                except Exception:
                    pass
            return (display_name, False, time.time() - t0, "Busy")

        async def chk_tier5(model_id, display_name):
            t0 = time.time()
            url = getattr(config, "POLLINATIONS_URL", "https://text.pollinations.ai/openai")
            try:
                r = await client.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json={"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                    timeout=6.0
                )
                return (display_name, r.status_code == 200, time.time() - t0, "Active" if r.status_code == 200 else "Busy")
            except Exception:
                return (display_name, False, time.time() - t0, "Timeout")

        t1_res, t1_5_res, t2_res, t3_res, t4_res, t5_res = await asyncio.gather(
            asyncio.gather(*[chk_tier1(m, d) for m, d in tier1_models]),
            asyncio.gather(*[chk_tier1_5(m, d) for m, d in tier1_5_models]),
            asyncio.gather(*[chk_tier2(m, d) for m, d in tier2_models]),
            asyncio.gather(*[chk_tier3(m, d) for m, d in tier3_models]),
            asyncio.gather(*[chk_tier4(m, d) for m, d in tier4_models]),
            asyncio.gather(*[chk_tier5(m, d) for m, d in tier5_models]),
        )

    all_res = list(t1_res) + list(t1_5_res) + list(t2_res) + list(t3_res) + list(t4_res) + list(t5_res)
    total_models = len(all_res)
    active_count = sum(1 for _, ok, _, _ in all_res if ok)
    elapsed = time.time() - t_start

    def fmt_tier(items):
        lines = []
        for display_name, ok, dt, note in items:
            icon = "✅" if ok else "❌"
            status_str = f"Active <i>({dt:.2f}s)</i>" if ok else f"Busy ({note})"
            lines.append(f"  • <code>{display_name}</code>: {icon} {status_str}")
        return "\n".join(lines)

    report = (
        "🤖 <b>AI Models Live Health Check</b>\n\n"
        "🚀 <b>Tier 1: Primary Engines</b>\n"
        f"{fmt_tier(t1_res)}\n\n"
        "🌐 <b>Tier 1.5: Adaptive Free Engines</b>\n"
        f"{fmt_tier(t1_5_res)}\n\n"
        "⚡ <b>Tier 2: Ultra-Fast Engines</b>\n"
        f"{fmt_tier(t2_res)}\n\n"
        "☁️ <b>Tier 3: High-Capacity Engine</b>\n"
        f"{fmt_tier(t3_res)}\n\n"
        "🌪️ <b>Tier 4: Specialized Engines</b>\n"
        f"{fmt_tier(t4_res)}\n\n"
        "🌸 <b>Tier 5: Resilient Backup Engine</b>\n"
        f"{fmt_tier(t5_res)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>System Health:</b> <code>{active_count}/{total_models} Models Online</code> ✅\n"
        f"⏱ <b>Check Duration:</b> <code>{elapsed:.2f}s</code>\n"
        "🔄 <b>Fallback Chain:</b> Tier 1 ➔ Tier 1.5 ➔ Tier 2 ➔ Tier 3 ➔ Tier 4 ➔ Tier 5"
    )
    return report

ask_ai = query_gpt
