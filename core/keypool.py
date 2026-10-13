"""Unified API-key orchestration for ALL providers."""
import logging
import time

import config

log = logging.getLogger("hwh.keypool")
_circuit_open = False


def mark_key_failed(key: str, status_code: int = 0) -> None:
    """Quarantine key based on error type:
    - 401/402: 24h quarantine (86400s)
    - 429: 25s cooldown
    - Other/timeout/5xx: 20s cooldown
    """
    if status_code in (401, 402):
        cooldown = 86400
    elif status_code == 429:
        cooldown = 25
    else:
        cooldown = 20
    config._exhausted_keys[key] = time.time() + cooldown
    _advance_pointer(config.APINEX_API_KEYS, key)
    try:
        import json
        sec_log = logging.getLogger("hwh.security")
        sec_log.warning(json.dumps({
            "event": "key_quarantined",
            "status_code": status_code,
            "cooldown": cooldown,
            "reason": f"HTTP {status_code}" if status_code else "network_error"
        }))
    except Exception:
        pass
    check_circuit_breaker()


def mark_key_ok(key: str) -> None:
    """Clear key from exhaustion map and advance pointer for round-robin distribution."""
    config._exhausted_keys.pop(key, None)
    if config.APINEX_API_KEYS and key in config.APINEX_API_KEYS:
        config._current_key_idx = (config.APINEX_API_KEYS.index(key) + 1) % len(config.APINEX_API_KEYS)
    check_circuit_breaker()


report_key_error = mark_key_failed
report_key_success = mark_key_ok


def check_circuit_breaker() -> bool:
    """Returns True if circuit breaker is open (all keys exhausted).
    Logs ERROR exactly once on state change."""
    global _circuit_open
    all_down = not has_healthy_keys()
    if all_down != _circuit_open:
        _circuit_open = all_down
        if _circuit_open:
            log.error("Circuit breaker OPEN: All Apinex API keys are quarantined or in cooldown.")
        else:
            log.info("Circuit breaker CLOSED: Healthy Apinex API key restored.")
    return _circuit_open


def _advance_pointer(keys, key):
    if not keys:
        return
    curr_idx = getattr(config, "_current_key_idx", 0)
    if key is None or key not in keys:
        config._current_key_idx = (curr_idx + 1) % len(keys)
    elif key == keys[curr_idx % len(keys)]:
        config._current_key_idx = (curr_idx + 1) % len(keys)


def healthy_keys(count: int = 1) -> list[str]:
    ordered = config.get_ordered_apinex_keys(only_healthy=True)
    return ordered[:count] if ordered else []


def has_healthy_keys() -> bool:
    return len(healthy_keys()) > 0


def active_key() -> str | None:
    ordered = config.get_ordered_apinex_keys(only_healthy=True)
    if ordered:
        return ordered[0]
    all_keys = config.get_ordered_apinex_keys()
    return all_keys[0] if all_keys else None
