"""Provider call retry with exponential backoff + jitter."""
import asyncio
import logging
import random

log = logging.getLogger("hwh.retry")

DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 16.0


async def with_backoff(fn, max_retries=DEFAULT_MAX_RETRIES, base_delay=DEFAULT_BASE_DELAY, max_delay=DEFAULT_MAX_DELAY, jitter=0.3):
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            res = fn()
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                return await res
            return res
        except Exception as e:
            last = e
            if attempt >= max_retries:
                break
            wait = min(base_delay * (2 ** (attempt - 1)), max_delay)
            wait += random.uniform(-jitter * wait, jitter * wait)
            log.debug("Retry attempt %d/%d failed: %s, waiting %.2fs", attempt, max_retries, e, wait)
            await asyncio.sleep(wait)
    raise RuntimeError("All %d attempts failed: %s" % (max_retries, last)) from last
