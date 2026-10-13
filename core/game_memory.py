"""Temporary per-round answer memory.

Keyed by (chat_id, round_key). Tracks which words were chosen, confirmed,
rejected, or merely observed so the bot does not resubmit them in the same round.

- Round key: "Round: N/M" for Octopus, puzzle message id for other games.
- TTL: entries expire (default 15 min). Cleared on round change or game over.
- In-memory only: restart clears it (accepted).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

TTL_SECONDS = 15 * 60

CHOSEN = "CHOSEN"          # we submitted it
CONFIRMED = "CONFIRMED"    # game confirmed it (any player)
REJECTED = "REJECTED"      # game said it is wrong / not allowed
OBSERVED = "OBSERVED"      # someone else typed it; not confirmed


@dataclass
class Entry:
    state: str
    player_id: int | None
    ts: float


_rounds: dict[tuple[int, str], dict[str, Entry]] = {}
_round_seen: dict[int, str] = {}  # chat_id -> latest round key


def _key(chat_id: int, round_key: str) -> tuple[int, str]:
    return (chat_id, round_key)


def _prune(bucket: dict[str, Entry], now: float) -> None:
    for w in [w for w, e in bucket.items() if now - e.ts > TTL_SECONDS]:
        del bucket[w]


def start_round(chat_id: int, round_key: str) -> None:
    """Call when a new round is detected. Drops memory of any previous round in this chat."""
    prev = _round_seen.get(chat_id)
    if prev is not None and prev != round_key:
        _rounds.pop(_key(chat_id, prev), None)
    _round_seen[chat_id] = round_key
    _rounds.setdefault(_key(chat_id, round_key), {})


def record(chat_id: int, round_key: str, word: str, state: str, player_id: int | None = None) -> None:
    w = (word or "").strip().upper()
    if not w:
        return
    now = time.time()
    bucket = _rounds.setdefault(_key(chat_id, round_key), {})
    _prune(bucket, now)
    # Stronger states are never downgraded to OBSERVED.
    old = bucket.get(w)
    if old and old.state in (CHOSEN, CONFIRMED, REJECTED) and state == OBSERVED:
        return
    bucket[w] = Entry(state, player_id, now)


def blocked_words(chat_id: int, round_key: str) -> set[str]:
    """Words that must not be proposed again in this round."""
    now = time.time()
    bucket = _rounds.get(_key(chat_id, round_key), {})
    _prune(bucket, now)
    return {w for w, e in bucket.items() if e.state in (CHOSEN, CONFIRMED, REJECTED)}


def state_of(chat_id: int, round_key: str, word: str) -> str | None:
    e = _rounds.get(_key(chat_id, round_key), {}).get((word or "").upper())
    return e.state if e else None


_meta: dict[tuple[int, str], dict] = {}


def set_round_meta(chat_id: int, round_key: str, mask: str, length: int, start_msg_id: int) -> None:
    """Remember the current round's mask and length so confirmations can be matched to a word."""
    _meta[_key(chat_id, round_key)] = {"mask": mask, "length": length, "start_msg_id": start_msg_id}


def round_meta(chat_id: int, round_key: str) -> dict | None:
    return _meta.get(_key(chat_id, round_key))


def current_round(chat_id: int) -> str | None:
    return _round_seen.get(chat_id)


def end_round(chat_id: int, round_key: str | None = None) -> None:
    """Clear memory for the given round, or the chat's latest round if None."""
    rk = round_key or _round_seen.get(chat_id)
    if rk is not None:
        _rounds.pop(_key(chat_id, rk), None)
    if round_key is None or _round_seen.get(chat_id) == round_key:
        _round_seen.pop(chat_id, None)


def clear_all() -> None:
    _rounds.clear()
    _round_seen.clear()
