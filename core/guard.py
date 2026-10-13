"""Spam and abuse guard for non-owner, non-sudo users.

- Rate limit: more than RATE_LIMIT commands in RATE_WINDOW seconds = 1 strike.
- Bad words: messages matching badwords.txt = 1 strike, command ignored.
- Strike 1 and 2 -> warning. Strike 3 -> restricted for RESTRICT_SECONDS, strikes reset.
- Owner and sudo are never checked (caller decides via is_exempt flag).
- State is in-memory (resets on restart).
"""
from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from collections import deque

log = logging.getLogger("hwh.guard")

RATE_LIMIT = 10
RATE_WINDOW = 60.0
MAX_STRIKES = 3
RESTRICT_SECONDS = 15 * 60
BADWORDS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "badwords.txt")

_history: dict[int, deque] = {}
_strikes: dict[int, int] = {}
_restricted_until: dict[int, float] = {}

_badwords_cache: dict = {"mtime": None, "patterns": []}
_badwords_missing_logged = False


def _load_badwords() -> list[re.Pattern]:
    """Loads badwords.txt, cached by mtime. Missing file = empty list (logged once)."""
    global _badwords_missing_logged
    try:
        mtime = os.path.getmtime(BADWORDS_FILE)
    except OSError:
        if not _badwords_missing_logged:
            log.info("badwords.txt not found; bad-word filter disabled.")
            _badwords_missing_logged = True
        return []
    if _badwords_cache["mtime"] == mtime:
        return _badwords_cache["patterns"]

    patterns: list[re.Pattern] = []
    with open(BADWORDS_FILE, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            word = normalize(line)
            if word:
                patterns.append(re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)"))
    _badwords_cache["mtime"] = mtime
    _badwords_cache["patterns"] = patterns
    return patterns


def normalize(text: str) -> str:
    """Lowercase, strip zero-width chars, collapse spaces."""
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", t)
    return " ".join(t.split())


def contains_badword(text: str) -> bool:
    """True if any bad word/phrase from badwords.txt appears as a whole word."""
    if not text:
        return False
    norm = normalize(text)
    return any(p.search(norm) for p in _load_badwords())


def _add_strike(user_id: int, now: float) -> tuple[int, bool]:
    """Adds one strike. Returns (strike_count_now, restricted_now)."""
    count = _strikes.get(user_id, 0) + 1
    if count >= MAX_STRIKES:
        _restricted_until[user_id] = now + RESTRICT_SECONDS
        _strikes[user_id] = 0
        return count, True
    _strikes[user_id] = count
    return count, False


def is_restricted(user_id: int, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    until = _restricted_until.get(user_id)
    if until is None:
        return False
    if now >= until:
        _restricted_until.pop(user_id, None)
        return False
    return True


def check_command(user_id: int, text: str, now: float | None = None) -> str:
    """Evaluates one command from a non-exempt user.

    Returns one of:
      "ok"          -> run the command
      "silent"      -> restricted user, ignore silently
      "warn:<n>"    -> strike n (1 or 2), send warning, ignore command
      "restricted"  -> strike 3, send restriction notice, ignore command
    """
    now = time.monotonic() if now is None else now

    if is_restricted(user_id, now):
        return "silent"

    dq = _history.setdefault(user_id, deque())
    while dq and now - dq[0] > RATE_WINDOW:
        dq.popleft()
    dq.append(now)

    if len(dq) > RATE_LIMIT:
        dq.clear()
        count, restricted = _add_strike(user_id, now)
        return "restricted" if restricted else f"warn:{count}"

    if contains_badword(text):
        count, restricted = _add_strike(user_id, now)
        return "restricted" if restricted else f"warn:{count}"

    return "ok"


def reset(user_id: int | None = None) -> None:
    """Clears state for one user or everyone (used by tests)."""
    if user_id is None:
        _history.clear()
        _strikes.clear()
        _restricted_until.clear()
    else:
        _history.pop(user_id, None)
        _strikes.pop(user_id, None)
        _restricted_until.pop(user_id, None)
