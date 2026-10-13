"""Octopus game (@OctopusEN_Bot): detection and parsing.

Start/state message:
    🏆 Difficulty: hard
    💵 Point: 2.8
    📌 Round: 2/100
    📚 9 letter: t r a L n a e s c
    🎲 L a _ _ _ _ t _ _

Confirmation line (sent after a correct answer, no tag):
    🌟 Яihitø♦️ (2 points) got it right! → leaving ✅
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from core import chat_buffer

ROUND_RE = re.compile(r"Round\s*:\s*(\d+)\s*/\s*(\d+)", re.I)
LETTER_RE = re.compile(r"(\d+)\s*letters?\s*:\s*([^\r\n]+)", re.I)
MASK_LINE_RE = re.compile(r"^[^\w_]*((?:[A-Za-z_]\s*)+)$", re.M)
CONFIRM_RE = re.compile(
    r"^(?P<name>.+?)\s*\((?P<pts>\d+)\s*points?\)\s*got\s+it\s+right",
    re.I | re.M,
)
MASK_CHARS = "_⁎∗＊*"


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def is_octopus_message(text: str) -> bool:
    """Round header with N-letter label and a mask line containing underscores."""
    t = _norm(text)
    if not ROUND_RE.search(t) or not LETTER_RE.search(t):
        return False
    return any("_" in line for line in t.splitlines())


def is_confirmation(text: str) -> bool:
    return bool(CONFIRM_RE.search(_norm(text)))


def parse_state(text: str) -> dict[str, Any] | None:
    """Round number, letter pool, target length, and mask for an Octopus state message."""
    t = _norm(text)
    rm = ROUND_RE.search(t)
    lm = LETTER_RE.search(t)
    if not rm or not lm:
        return None

    length = int(lm.group(1))
    letters = [c.upper() for c in re.findall(r"[A-Za-z]", lm.group(2))]

    mask = None
    for line in t.splitlines():
        if "_" not in line or ROUND_RE.search(line) or LETTER_RE.search(line):
            continue
        cleaned = re.sub(r"^[^\w_]+", "", line.strip())
        tokens = cleaned.split()
        if tokens and all(len(tok) == 1 and (tok.isalpha() or tok == "_") for tok in tokens):
            mask = " ".join(tokens)
            break
    if mask is None:
        return None

    return {
        "round_no": int(rm.group(1)),
        "round_total": int(rm.group(2)),
        "round_key": f"round:{rm.group(1)}/{rm.group(2)}",
        "length": length,
        "letters": letters,
        "mask": mask,
    }


def parse_confirmation(text: str) -> dict[str, Any] | None:
    m = CONFIRM_RE.search(_norm(text))
    if not m:
        return None
    return {"name": m.group("name").strip(), "points": int(m.group("pts"))}


def mask_matches(word: str, mask: str) -> bool:
    """Word must have same letter count as mask tokens and match every visible letter."""
    tokens = [tok for tok in mask.split() if tok]
    w = word.upper()
    if len(w) != len(tokens):
        return False
    for ch, tok in zip(w, tokens):
        if tok in MASK_CHARS:
            continue
        if ch != tok.upper():
            return False
    return True


def find_answer_word(chat_id: int, sender_id: int | None, mask: str, length: int,
                     round_start_msg_id: int | None) -> str | None:
    """Latest single-word message from sender_id, after the round started, that fits the mask."""
    for m in reversed(chat_buffer.recent(chat_id)):
        if sender_id is not None and m.sender_id != sender_id:
            continue
        if round_start_msg_id is not None and m.message_id <= round_start_msg_id:
            continue
        candidate = m.text.strip()
        if " " in candidate or not candidate.isalpha():
            continue
        if len(candidate) == length and mask_matches(candidate, mask):
            return candidate.upper()
    return None
