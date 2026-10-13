"""OpenAgentic.id pool: free models via OpenAI-compatible API proxy.

OpenAgentic.id provides 37+ AI models (Claude, GPT, DeepSeek, GLM, etc.)
through a single OpenAI-compatible API endpoint.
Uses a single API key with sequential model fallback.

Tier placement: After Apinex (Tier 1), before Groq (Tier 2).
"""
from __future__ import annotations

import json
import logging
import re

import httpx

import config

log = logging.getLogger("hwh.openagentic")

PER_MODEL_TIMEOUT = 8.0


def _extract_content(body: str) -> str:
    """Pull assistant text from a chat-completions JSON body."""
    try:
        data = json.loads(body)
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        pass
    m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
    if not m:
        return ""
    try:
        return json.loads(f'"{m.group(1)}"').strip()
    except Exception:
        return m.group(1).strip()


def _get_keys() -> list[str]:
    keys = getattr(config, "OPENAGENTIC_API_KEYS", [])
    if not keys and getattr(config, "OPENAGENTIC_API_KEY", ""):
        keys = [config.OPENAGENTIC_API_KEY]
    return [k for k in keys if k]


async def query(messages: list[dict], model: str | None = None, temperature: float = 0.4,
                max_tokens: int = 1500) -> str | None:
    """Query OpenAgentic.id free models. Returns assistant text or None.

    Iterates through configured API keys and models sequentially.
    """
    keys = _get_keys()
    if not keys:
        log.debug("OpenAgentic: no API keys configured, skipping.")
        return None

    base_url = config.OPENAGENTIC_BASE_URL
    models = [model] if model else getattr(config, "OPENAGENTIC_FREE_MODELS", [])
    if not models:
        return None

    async with httpx.AsyncClient(timeout=PER_MODEL_TIMEOUT) as http:
        for api_key in keys:
            key_id = api_key[:10] + "..." if len(api_key) > 10 else api_key
            for mdl in models:
                try:
                    r = await http.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": mdl,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                        },
                    )
                except Exception as err:
                    log.warning("OpenAgentic key %s model %s network error: %s", key_id, mdl, type(err).__name__)
                    continue

                if r.status_code == 200:
                    content = _extract_content(r.text)
                    if content:
                        log.info("OpenAgentic answered via key %s model %s.", key_id, mdl)
                        return content
                    log.warning("OpenAgentic model %s returned empty content.", mdl)
                    continue

                if r.status_code in (401, 403):
                    log.warning("OpenAgentic key %s auth error HTTP %s: %s", key_id, r.status_code, r.text[:80])
                    break  # Key not authorized or needs topup, try next key

                if r.status_code == 429:
                    log.info("OpenAgentic model %s rate-limited (429), trying next...", mdl)
                    continue

                log.warning("OpenAgentic model %s HTTP %s: %s", mdl, r.status_code, r.text[:80])

    return None


async def query_vision(image_b64: str, mime: str, prompt: str,
                       system_prompt: str = "") -> str | None:
    """Query OpenAgentic.id vision-capable free models with an image.

    Returns assistant text or None.
    """
    keys = _get_keys()
    if not keys:
        return None

    base_url = config.OPENAGENTIC_BASE_URL
    models = getattr(config, "OPENAGENTIC_VISION_MODELS", [])
    if not models:
        return None

    messages = [
        {"role": "system", "content": system_prompt} if system_prompt else None,
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        },
    ]
    messages = [m for m in messages if m is not None]

    async with httpx.AsyncClient(timeout=30.0) as http:
        for api_key in keys:
            key_id = api_key[:10] + "..." if len(api_key) > 10 else api_key
            for mdl in models:
                try:
                    r = await http.post(
                        f"{base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": mdl,
                            "messages": messages,
                            "temperature": 0.3,
                            "max_tokens": 1500,
                        },
                    )
                except Exception as err:
                    log.warning("OpenAgentic vision %s error: %s", mdl, type(err).__name__)
                    continue

                if r.status_code == 200:
                    content = _extract_content(r.text)
                    if content:
                        log.info("OpenAgentic vision answered via key %s model %s.", key_id, mdl)
                        return content
                    continue

                if r.status_code in (401, 403):
                    log.warning("OpenAgentic vision key %s auth error HTTP %s.", key_id, r.status_code)
                    break

                if r.status_code == 429:
                    log.info("OpenAgentic vision model %s rate-limited, trying next...", mdl)
                    continue

                log.warning("OpenAgentic vision %s HTTP %s.", mdl, r.status_code)

    return None

