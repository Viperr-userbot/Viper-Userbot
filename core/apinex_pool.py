"""Apinex key pool: random start, sequential fallback, one model, 5 RPM per key.

Rules (from plan):
- Build a random healthy order of keys. Try them one after another.
- Stop at the first HTTP 200 with non-empty content.
- Each key may make at most RPM_LIMIT requests in any RPM_WINDOW seconds.
  A key at the limit is skipped without a network call.
- Failures (401/402/404/429/5xx/timeouts) cool the key via config.report_key_error
  and move on to the next key immediately.
- Total time budget per call; the loop stops once the budget is spent.
- Logs key prefix only, never the full key.
"""
from __future__ import annotations

import logging
import random
import time
from collections import deque

import httpx

import config

log = logging.getLogger("hwh.apinex")

RPM_LIMIT = 5
RPM_WINDOW = 60.0
PER_KEY_TIMEOUT = 4.0
DEFAULT_BUDGET = 10.0

_history: dict[str, deque] = {}


def _prune(key: str, now: float) -> deque:
    dq = _history.setdefault(key, deque())
    while dq and now - dq[0] > RPM_WINDOW:
        dq.popleft()
    return dq


def _under_rpm(key: str, now: float) -> bool:
    return len(_prune(key, now)) < RPM_LIMIT


def _record(key: str, now: float) -> None:
    _prune(key, now).append(now)


def pick_keys() -> list[str]:
    """Healthy keys in random order, skipping keys at the 5 RPM limit."""
    now = time.time()
    healthy = config.get_ordered_apinex_keys(only_healthy=True)  # already random-shuffled
    random.shuffle(healthy)
    return [k for k in healthy if _under_rpm(k, now)]


def _extract_content(body: str) -> str:
    import json
    import re

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


async def query(messages: list[dict], model: str | None = None, temperature: float = 0.4,
                max_tokens: int = 1500, budget: float = DEFAULT_BUDGET) -> str | None:
    """Return assistant text from the first Apinex key that answers, or None."""
    keys = pick_keys()
    if not keys:
        log.info("Apinex: no healthy key under 5 RPM right now.")
        return None

    model = model or config.GPT_MODEL
    deadline = time.time() + budget
    tried = 0

    async with httpx.AsyncClient(timeout=PER_KEY_TIMEOUT) as http:
        for key in keys:
            if time.time() >= deadline:
                log.info("Apinex: budget %.1fs spent after %d key(s).", budget, tried)
                break
            tried += 1
            _record(key, time.time())
            try:
                r = await http.post(
                    f"{config.APINEX_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": messages,
                          "temperature": temperature, "max_tokens": max_tokens},
                )
            except Exception as err:
                log.warning("Apinex key %s network error: %s", key[:8], type(err).__name__)
                config.report_key_error(key, 0)
                continue

            if r.status_code == 200:
                content = _extract_content(r.text)
                if content:
                    config.report_key_success(key)
                    log.info("Apinex answered via key %s (try %d).", key[:8], tried)
                    return content
                log.warning("Apinex key %s returned empty content.", key[:8])
                continue

            log.warning("Apinex key %s HTTP %s (try %d).", key[:8], r.status_code, tried)
            config.report_key_error(key, r.status_code)

    return None
