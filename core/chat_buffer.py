"""Per-chat ring buffer of recent group messages.

Used to find which player submitted a word before a confirmation line
("<name> (N points) got it right!"). Filled by the existing GC handler.
No network calls. In-memory only.
"""
from __future__ import annotations

import time
import unicodedata
from collections import deque
from dataclasses import dataclass

MAX_PER_CHAT = 20


@dataclass(frozen=True)
class BufferedMsg:
    message_id: int
    sender_id: int | None
    display_name: str
    text: str
    ts: float


_buffers: dict[int, deque] = {}


def norm_name(name: str) -> str:
    """Normalized, lowercased name for comparison. Keeps letters from all scripts."""
    s = unicodedata.normalize("NFKC", name or "").casefold().strip()
    return " ".join(s.split())


def add(chat_id: int, message_id: int, sender_id: int | None, display_name: str, text: str) -> None:
    dq = _buffers.setdefault(chat_id, deque(maxlen=MAX_PER_CHAT))
    dq.append(BufferedMsg(message_id, sender_id, display_name or "", text or "", time.time()))


def recent(chat_id: int) -> list[BufferedMsg]:
    return list(_buffers.get(chat_id, ()))


def clear(chat_id: int) -> None:
    _buffers.pop(chat_id, None)


def find_sender_id(chat_id: int, display_name: str) -> int | None:
    """Map a display name from a result line to a sender id (latest match wins)."""
    target = norm_name(display_name)
    if not target:
        return None
    for m in reversed(recent(chat_id)):
        if m.sender_id is not None and norm_name(m.display_name) == target:
            return m.sender_id
    return None
