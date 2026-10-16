import asyncio
import logging
import json
import signal
import sys
import time
import os
import re
import random
import html
import httpx
import unicodedata
import shutil
import zipfile
import io
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from telethon import TelegramClient, events
import config
import llm_vision
import notifier
import solver
import database
import ten_things
import wordly
import kang
import gpt_service
import extra_plugins
import help_menu
from core import guard

from core.auth import ctx_is_owner, ctx_is_sudo
from core import router as core_router

log = config.setup_logging()
sec_log = logging.getLogger("hwh.security")

def audit_log(event: str, actor_id: int | None = None, chat_id: int | None = None, reason: str = "", **extra):
    sec_log.info(json.dumps({
        "event": event,
        "actor_id": actor_id,
        "chat_id": chat_id,
        "reason": reason,
        **extra
    }))

session_data = config.get_telethon_session()
client = TelegramClient(
    session_data,
    config.SANGMATA_API_ID,
    config.SANGMATA_API_HASH,
    connection_retries=None,
    retry_delay=2,
    auto_reconnect=True,
    flood_sleep_threshold=120,
    request_retries=10,
)
BOT_START_TIME = time.time()

def get_readable_uptime() -> str:
    """Returns human-readable uptime string (e.g. 2d 5h 30m 12s)."""
    elapsed = max(0, int(time.time() - BOT_START_TIME))
    days, rem = divmod(elapsed, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if mins > 0:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    return " ".join(parts)

busy: set[int] = set()
_trigger_debounce: dict[int, float] = {}
_grab_debounce: dict[int, float] = {}
_chat_locks: dict[int, asyncio.Lock] = {}

_active_tasks: dict[int, list[asyncio.Task]] = {}
_chat_queues: dict[int, asyncio.Queue] = {}

async def process_chat_queue(chat_id: int):
    queue = _chat_queues[chat_id]
    while True:
        try:
            coro = await queue.get()
            task = asyncio.create_task(coro)
            if chat_id not in _active_tasks:
                _active_tasks[chat_id] = []
            _active_tasks[chat_id].append(task)
            try:
                await task
            except asyncio.CancelledError:
                log.info(f"Task for chat {chat_id} was cancelled.")
            except Exception as e:
                log.error(f"Task in chat {chat_id} failed: {e}", exc_info=True)
            finally:
                if chat_id in _active_tasks and task in _active_tasks[chat_id]:
                    _active_tasks[chat_id].remove(task)
                queue.task_done()
        except Exception as e:
            log.error(f"Error in queue processor for {chat_id}: {e}")
            await asyncio.sleep(1)

def enqueue_heavy_task(chat_id: int, coro):
    if chat_id not in _chat_queues:
        _chat_queues[chat_id] = asyncio.Queue()
        asyncio.create_task(process_chat_queue(chat_id))
    _chat_queues[chat_id].put_nowait(coro)
_last_sent_gc_messages: dict[int, Any] = {}
_game_id = None

async def latest_game_message(chat_id):
    global _game_id
    game_bot_username = config.GAME_BOT
    if _game_id is None:
        try:
            _game_id = (await client.get_entity(game_bot_username)).id
        except Exception as e:
            log.warning("Could not resolve game bot entity %s: %s", game_bot_username, e)
            _game_id = None

    candidates = []
    async for m in client.iter_messages(chat_id, limit=25):

        if _game_id and m.sender_id == _game_id:
            if m.media or ten_things.is_tenthings_message(m.raw_text) or wordly.is_wordly_message(m.raw_text):
                return m

        elif wordly.is_wordly_message(m.raw_text):
            return m

        elif ten_things.is_tenthings_message(m.raw_text):
            return m

        elif m.media and m.raw_text and solver.HINT_RE.search(m.raw_text):
            return m

        elif m.sender and getattr(m.sender, "bot", False):
            if m.media or (m.raw_text and len(m.raw_text.strip()) > 10):
                candidates.append(m)
        elif m.media:
            candidates.append(m)

    return candidates[0] if candidates else None

def format_report(results, disputed, secs) -> str:
    lines = ["🛰️ <b>Answers</b>"]
    for r in results:
        if not r.best:
            blanks = "_" * max(1, r.hint.length - 2)
            lines.append(f"❓ <b>{r.hint.first}{blanks}{r.hint.last}</b> ({r.hint.length} letters) — not found")
            continue
        c = r.best
        in_dict = c.word in solver.WORDS
        if in_dict and not c.approx:
            mark = "✅"
        elif in_dict:
            mark = "🔶"
        else:
            mark = "⚠️"
        lines.append(
            f"{mark} <b>{c.word}</b>  <code>R{c.start[0]+1}C{c.start[1]+1} {c.arrow} R{c.end[0]+1}C{c.end[1]+1}</code>"
        )
    lines.append("")
    lines.append(f"⏱ {secs:.1f}s" + (f" | ⚠️ {disputed} cell(s) par LLM votes alag the" if disputed else ""))
    return "\n".join(lines)

async def ai_rescue_uncertain_hints(results: list[solver.Result]):
    """
    Calls AI only when needed ('jahan jaroorat pde'):
    If a hint has no candidate, or the candidate word is not in dictionary.
    Staggers requests slightly to prevent bursting any single API key.
    """
    needs_ai = [
        r for r in results
        if not r.best or (r.best.word not in solver.WORDS and r.best.score < 950)
    ]
    if not needs_ai:
        return

    async def rescue_single(res: solver.Result, idx: int = 0):
        h = res.hint
        blanks = "_" * max(1, h.length - 2)
        pattern = f"{h.first}{blanks}{h.last}" if h.last else f"{h.first}{blanks}"
        curr_word = res.best.word if res.best else ""

        prompt = (
            f"Word puzzle: Hint is {pattern} ({h.length} letters)."
            + (f" Letters found in grid: {curr_word} (may have OCR typo)." if curr_word else "")
            + f" What is the uppercase English word of exactly {h.length} letters? Return ONLY the word."
        )
        try:

            if idx > 0:
                await asyncio.sleep(idx * 0.2)
            content = await gpt_service.query_gpt(prompt=prompt)
            words = re.findall(r"\b[A-Za-z]{" + str(h.length) + r"}\b", content)
            valid_words = [
                w.upper() for w in words
                if (not h.first or w[0].upper() == h.first) and (not h.last or w[-1].upper() == h.last)
            ]
            if valid_words:
                ai_word = valid_words[0]
                if res.best:
                    res.best.word = ai_word
                    res.best.approx = False
                else:
                    res.cands.insert(0, solver.Cand(ai_word, (0, 0), (0, 0), "🤖", approx=False, score=950.0))
        except Exception as e:
            log.warning("AI rescue failed for %s: %s", pattern, e)

    await asyncio.gather(*[rescue_single(r, i) for i, r in enumerate(needs_ai)])

async def solve_message(msg, extra_chat_id: int | None = None, reply_msg=None, target_user_id: int | None = None):
    t0 = time.time()

    raw_task = asyncio.create_task(client.download_media(msg, file=bytes))
    hints = solver.parse_hints(msg.raw_text or "")
    if not hints:
        raw_task.cancel()
        raise RuntimeError("Caption mein game hints nahi mile")
    raw = await raw_task

    grid, disputed = await llm_vision.read_grid(raw)

    results = solver.solve(grid, hints)

    needs_rescue = any(
        not r.best or (r.best.word not in solver.WORDS and r.best.score < 950)
        for r in results
    )
    if needs_rescue:
        await ai_rescue_uncertain_hints(results)

    secs = time.time() - t0
    final_report = format_report(results, disputed, secs)

    if reply_msg:
        try:
            await reply_msg.edit(final_report, parse_mode="html")
        except Exception:
            try: await reply_msg.reply(final_report, parse_mode="html")
            except Exception: pass

    if extra_chat_id and database.is_userbot_gc_reply_enabled():
        await play_and_confirm_wordhunt_gc(
            extra_chat_id, msg, raw, grid, hints, results, target_user_id=target_user_id
        )

    await notifier.text(final_report, target_user_id=target_user_id)

def format_game_answer_casing(word: str) -> str:
    """
    Randomizes answer casing between UPPERCASE and lowercase:
    Applies across all games (Wordly, Grid Word Hunt, 10Things, Anagrams, etc.)
    Fulfills user requirement: 'sab game ye daal do kbhi upper case me answer aaye kbhi lower case me aaye answer'
    """
    if not word:
        return word
    style = random.choice(["upper", "lower"])
    if style == "upper":
        return word.upper()
    else:
        return word.lower()

async def send_game_answers_to_gc(
    target_gc: int | None,
    words: list[str],
    reply_to_id: int | None = None
) -> list[str]:
    """
    Sends game answers directly to authorized GC from the userbot account.
    CRITICAL RULES:
    1. Sends ONLY 1 word per message (never multi-word reports or coordinates/arrows).
    2. Randomizes casing between UPPERCASE and lowercase for natural, humanlike gameplay.
    3. If humanize is ON, shows typing indicator and adds realistic typing delay (1s - 8s) between words.
    4. If humanize is OFF, uses a small safety delay (0.8s) to avoid Telegram flood limits.
    5. Returns list of words successfully sent.
    """
    if not target_gc or not database.is_userbot_gc_reply_enabled() or not words:
        return []

    clean_words = []
    seen = set()
    for w in words:
        if not w:
            continue
        cleaned = re.sub(r"[^A-Za-z0-9]", "", str(w)).strip()
        if cleaned and cleaned.upper() not in seen:
            seen.add(cleaned.upper())
            clean_words.append(cleaned.upper())

    if not clean_words:
        return []

    sent = []
    for idx, word in enumerate(clean_words):
        try:
            if database.is_humanize_enabled():
                typing_sec = random.uniform(1.0, 8.0)
                try:
                    async with client.action(target_gc, 'typing'):
                        await asyncio.sleep(typing_sec)
                except Exception:
                    await asyncio.sleep(typing_sec)
            else:
                if idx > 0:
                    await asyncio.sleep(0.8)

            cased_word = format_game_answer_casing(word)

            sent_msg = await client.send_message(target_gc, cased_word, reply_to=reply_to_id)
            if sent_msg:
                _last_sent_gc_messages[target_gc] = sent_msg
            sent.append(word)
            log.info("GC Reply sent word '%s' (cased '%s') to %s", word, cased_word, target_gc)
        except Exception as e:
            log.warning("Failed to send answer '%s' to GC %s: %s", word, target_gc, e)

    return sent

async def delete_wrong_gc_word(
    target_gc: int,
    word: str,
    my_id: int | None = None,
    msg_obj: Any | None = None
) -> bool:
    """
    Deletes a wrong/rejected word sent by the userbot from the group chat,
    leaving only confirmed correct words in GC.
    """
    deleted = False

    if msg_obj:
        try:
            if hasattr(msg_obj, "delete"):
                await msg_obj.delete()
                deleted = True
            elif hasattr(msg_obj, "id"):
                await client.delete_messages(target_gc, [msg_obj.id])
                deleted = True
            if deleted:
                log.info("Deleted wrong word '%s' via msg_obj in GC %s", word, target_gc)
                return True
        except Exception as e:
            log.debug("Failed deleting wrong word via msg_obj: %s", e)

    tracked = _last_sent_gc_messages.pop(target_gc, None)
    if tracked:
        try:
            if hasattr(tracked, "delete"):
                await tracked.delete()
                deleted = True
            elif hasattr(tracked, "id"):
                await client.delete_messages(target_gc, [tracked.id])
                deleted = True
            if deleted:
                log.info("Deleted tracked wrong word '%s' in GC %s", word, target_gc)
                return True
        except Exception as e:
            log.debug("Failed deleting tracked wrong word: %s", e)

    try:
        res = await client.get_messages(target_gc, limit=6)
        msgs = res if isinstance(res, (list, tuple)) else ([res] if res else [])
        for m in msgs:
            m_text = re.sub(r"[^A-Za-z0-9]", "", getattr(m, "raw_text", "") or "").strip().upper()
            sender_id = getattr(m, "sender_id", None)
            is_mine = getattr(m, "out", False) or (my_id and sender_id == my_id)
            if is_mine and m_text == word.upper():
                try:
                    if hasattr(m, "delete"):
                        await m.delete()
                    else:
                        await client.delete_messages(target_gc, [m.id])
                    log.info("Found and deleted wrong word message '%s' in GC %s", word, target_gc)
                    return True
                except Exception as de:
                    log.debug("Error deleting matched wrong word msg: %s", de)
    except Exception as e:
        log.debug("Fallback search and delete error: %s", e)

    return False

def extract_bare_game_answers(solution_text: str) -> list[str]:
    """
    Extracts plain answer words (one word/answer each) from AI/solver responses,
    stripping arrows, coordinates, markdown bolding, numbering, and diagnostic symbols.
    """
    if not solution_text:
        return []
    answers = []
    lines = solution_text.strip().splitlines()
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue

        if any(cleaned.startswith(p) for p in ("⏱", "🛰️", "⚠️", "🎯", "Answers", "Solution:", "Time:")):
            continue
        if "Par LLM votes" in cleaned or "disputed" in cleaned or "not found" in cleaned:
            continue

        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"\b[Rr]\d+[Cc]\d+\s*[→←↑↓↘↙↗↖]\s*[Rr]\d+[Cc]\d+\b", " ", cleaned)

        cleaned = re.sub(r"^(?:\d+[\.\)]|[\-\*•])\s*", "", cleaned).strip()

        cleaned = re.sub(r"[\*`_~]", "", cleaned).strip()

        cleaned = re.sub(r"^[✅🔶⚠️❓]\s*", "", cleaned).strip()

        if "," in cleaned:
            for part in cleaned.split(","):
                w = re.sub(r"[^A-Za-z0-9]", "", part).strip()
                if w and len(w) >= 2:
                    answers.append(w.upper())
        else:
            w = re.sub(r"[^A-Za-z0-9]", "", cleaned).strip()
            if w and len(w) >= 2:
                answers.append(w.upper())
    return answers

async def maybe_userbot_gc_reply(target_gc: int | None, text_or_words: Any, reply_to_id: int | None = None):
    """
    Safe wrapper for sending answers to GC from userbot account.
    If passed a list of words, sends 1 word per message via send_game_answers_to_gc.
    If passed a multi-line or diagnostic report (with arrows), extracts the bare words
    so arrow reports NEVER leak into the group chat!
    """
    if not target_gc or not database.is_userbot_gc_reply_enabled():
        return
    if isinstance(text_or_words, list):
        await send_game_answers_to_gc(target_gc, text_or_words, reply_to_id=reply_to_id)
        return

    text = str(text_or_words or "").strip()
    if not text:
        return

    if any(sym in text for sym in ("🛰️", "🎯", "↘", "→", "←", "↑", "↓", "↗", "↙", "↖", "<code>R", "\n")):
        words = extract_bare_game_answers(text)
        if words:
            await send_game_answers_to_gc(target_gc, words, reply_to_id=reply_to_id)
        return

    await send_game_answers_to_gc(target_gc, [text], reply_to_id=reply_to_id)

def parse_game_bot_points_and_found(text: str) -> dict:
    """
    Parses game bot point award messages.
    Returns dict:
      'points': int or None
      'player': str or None (e.g. '@username', 'John', 'You')
      'word': str or None (e.g. 'APPLE')
    """
    if not text:
        return {"points": None, "player": None, "word": None}

    points = None
    player = None
    word = None

    m_pts = re.search(r"\+(\d+)\s*points?(?:\s+for\s+([^\n!.,]+))?", text, re.I)
    if m_pts:
        try:
            points = int(m_pts.group(1))
        except Exception:
            pass
        if m_pts.group(2):
            player = m_pts.group(2).strip()

    if not player:
        m_pl = re.search(r"points?\s+for\s+([^\n!.,]+)", text, re.I)
        if m_pl:
            player = m_pl.group(1).strip()

    m_found = re.search(r"(?:you|[A-Za-z0-9_@]+)\s+found\s+[\"']?([A-Za-z]{2,25})[\"']?", text, re.I)
    if m_found:
        word = m_found.group(1).upper()
    else:
        m_word = re.search(r"(?:word|answer):\s*[\"']?([A-Za-z]{2,25})[\"']?", text, re.I)
        if m_word:
            word = m_word.group(1).upper()
        else:
            m_got = re.search(r"(?:got|guessed)\s+[\"']?([A-Za-z]{2,25})[\"']?", text, re.I)
            if m_got:
                word = m_got.group(1).upper()

    return {"points": points, "player": player, "word": word}

def is_message_confirmation_for_me(
    text: str,
    my_id: int | None = None,
    my_username: str | None = None,
    my_first_name: str | None = None,
    my_last_name: str | None = None,
    msg_obj: Any | None = None,
    sent_msg_id: int | None = None
) -> tuple[bool, str | None]:
    """
    Checks if text is a game bot confirmation for the current userbot.
    Returns (is_my_confirmation, word_found).
    Matches:
    - "+3 points for <me>" / "+3 points for you"
    - "You found <WORD>!"
    - "<my_first_name> found <WORD>!"
    - "<@my_username> found <WORD>!"
    """
    if not text:
        return False, None

    data = parse_game_bot_points_and_found(text)
    word = data["word"]

    if msg_obj and sent_msg_id:
        reply_id = getattr(msg_obj, "reply_to_msg_id", None)
        if reply_id and reply_id == sent_msg_id:
            return True, word

    if msg_obj and hasattr(msg_obj, "entities") and msg_obj.entities:
        for ent in msg_obj.entities:
            ent_uid = getattr(ent, "user_id", None)
            if ent_uid and my_id and ent_uid == my_id:
                return True, word

    t_norm = unicodedata.normalize("NFKD", text)
    player_raw = data["player"] or ""
    player_norm = unicodedata.normalize("NFKD", player_raw).lower().strip()
    p_clean = re.sub(r"[^a-z0-9]", "", player_norm)

    me_names = ["you"]
    if my_username:
        u_clean = my_username.lower().lstrip("@")
        me_names.append(u_clean)
        me_names.append(f"@{u_clean}")
    if my_first_name:
        fn_clean = my_first_name.strip()
        me_names.append(fn_clean)
    if my_last_name:
        ln_clean = my_last_name.strip()
        me_names.append(ln_clean)
    if my_first_name and my_last_name:
        me_names.append(f"{my_first_name} {my_last_name}".strip())

    has_you_found = bool(re.search(r"\byou\s+found\b", t_norm, re.I))

    player_matches_me = False
    if p_clean:
        for name in me_names:
            n_norm = unicodedata.normalize("NFKD", name).lower().strip().lstrip("@")
            n_clean = re.sub(r"[^a-z0-9]", "", n_norm)
            if n_clean and (n_clean == p_clean or n_clean in p_clean or p_clean in n_clean):
                player_matches_me = True
                break

    if my_first_name and not player_matches_me:
        fn_esc = re.escape(unicodedata.normalize("NFKD", my_first_name).strip())
        if re.search(rf"\b{fn_esc}\s+found\b", t_norm, re.I):
            player_matches_me = True

    if my_username and not player_matches_me:
        un_esc = re.escape(my_username.lstrip("@").strip())
        if re.search(rf"@?{un_esc}\s+found\b", t_norm, re.I):
            player_matches_me = True

    if has_you_found or player_matches_me:
        return True, word
    return False, None

def is_message_confirmation_for_other(
    text: str,
    my_id: int | None = None,
    my_username: str | None = None,
    my_first_name: str | None = None,
    my_last_name: str | None = None,
    msg_obj: Any | None = None,
    sent_msg_id: int | None = None
) -> tuple[bool, str | None, str | None]:
    """
    Checks if text is a game bot confirmation awarded to ANOTHER user.
    Returns (is_other, word_found, player_name).
    """
    if not text:
        return False, None, None

    is_me, my_word = is_message_confirmation_for_me(
        text,
        my_id=my_id,
        my_username=my_username,
        my_first_name=my_first_name,
        my_last_name=my_last_name,
        msg_obj=msg_obj,
        sent_msg_id=sent_msg_id
    )
    if is_me:
        return False, None, None

    data = parse_game_bot_points_and_found(text)
    player = data["player"]
    word = data["word"]

    has_points = bool(data["points"] is not None or re.search(r"\+\d+\s*points?", text, re.I))
    has_found = bool(re.search(r"(?:[A-Za-z0-9_@]+)\s+found\b", text, re.I))

    if (has_points and player) or (has_found and word):
        return True, word, player
    return False, None, None

def extract_already_found_words(text: str) -> set[str]:
    """
    Extracts words that are already found or struck out in the game message:
    e.g. ~WORD~, <s>WORD</s>, [Found by ...], ✅ WORD, etc.
    """
    found = set()
    if not text:
        return found

    for w in re.findall(r"~([A-Za-z]{2,25})~", text):
        found.add(w.upper())

    for w in re.findall(r"<(?:s|strike|del)>([A-Za-z]{2,25})</(?:s|strike|del)>", text, re.I):
        found.add(w.upper())

    for m in re.finditer(r"([A-Za-z]{2,25})\s*[\(\[]\s*found\s+by[^\)\]]+[\)\]]", text, re.I):
        found.add(m.group(1).upper())

    for line in text.splitlines():
        line_s = line.strip()
        if "✅" in line_s or "found by" in line_s.lower():
            for w in re.findall(r"\b[A-Za-z]{2,25}\b", line_s):
                w_up = w.upper()
                if w_up not in ("FOUND", "BY", "POINTS", "POINT"):
                    found.add(w_up)

    return found

def find_alternative_grid_word(
    grid: list[list[str]],
    hint: solver.Hint,
    tried_words: set[str],
    cands: list[solver.Cand] | None = None
) -> str | None:
    """
    Finds an alternative candidate word in the grid for a hint that hasn't been tried yet:
    1. First checks pre-computed candidates `cands` from solver (sorted by score).
    2. If none, searches grid rays for any dictionary word matching hint.
    3. If none, falls back to dictionary words matching the hint constraints.
    """
    tried_up = {w.upper() for w in tried_words}

    if cands:
        for c in cands:
            cw = c.word.upper()
            if cw not in tried_up and len(cw) == hint.length:
                if (not hint.first or cw[0] == hint.first.upper()) and (not hint.last or cw[-1] == hint.last.upper()):
                    return cw

    if grid and len(grid) > 0:
        R, C = len(grid), len(grid[0])
        n = hint.length
        f = hint.first.upper() if hint.first else ''
        l = hint.last.upper() if hint.last else ''

        for r in range(R):
            for c in range(C):
                if f and grid[r][c] != f:
                    continue
                for arrow, (dr, dc) in solver.DIRS.items():
                    er, ec = r + dr * (n - 1), c + dc * (n - 1)
                    if 0 <= er < R and 0 <= ec < C:
                        if l and grid[er][ec] != l:
                            continue
                        ray = "".join(grid[r + dr * i][c + dc * i] for i in range(n))
                        if ray in solver.WORDS and ray not in tried_up:
                            return ray

    n = hint.length
    f = hint.first.upper() if hint.first else ''
    l = hint.last.upper() if hint.last else ''
    if f and l:
        dict_words = solver.WORDS_BY_HINT.get((n, f, l), [])
    elif f:
        dict_words = solver.WORDS_BY_LEN_FIRST.get((n, f), [])
    elif l:
        dict_words = solver.WORDS_BY_LEN_LAST.get((n, l), [])
    else:
        dict_words = solver.WORDS_BY_LEN.get(n, [])

    for dw in dict_words:
        if dw not in tried_up:
            return dw

    return None

async def wait_for_word_confirmation(
    target_gc: int,
    word: str,
    hint: solver.Hint,
    my_user_info: dict,
    game_msg_id: int | None = None,
    sent_msg_id: int | None = None,
    timeout: float = 4.0,
    check_interval: float = 0.3
) -> tuple[bool, str]:
    """
    Waits up to timeout seconds for game bot confirmation (+3 points / You found <word>) in target_gc.
    Returns (confirmed, reason):
    - (True, "confirmed_me"): Game bot confirmed +3 points for me / You found <word>
    - (False, "claimed_by_other"): Another player got points or found the word/hint
    - (False, "timeout"): No confirmation arrived within timeout (word was wrong / rejected)
    """
    t_end = time.time() + timeout
    my_id = my_user_info.get("id")
    my_uname = my_user_info.get("username")
    my_fname = my_user_info.get("first_name")
    my_lname = my_user_info.get("last_name")

    while time.time() < t_end:
        try:

            res_m = await client.get_messages(target_gc, limit=8)
            messages = res_m if isinstance(res_m, (list, tuple)) else ([res_m] if res_m else [])
            for m in messages:
                text = getattr(m, "raw_text", "") or ""
                if not text:
                    continue

                is_me, conf_word = is_message_confirmation_for_me(
                    text,
                    my_id=my_id,
                    my_username=my_uname,
                    my_first_name=my_fname,
                    my_last_name=my_lname,
                    msg_obj=m,
                    sent_msg_id=sent_msg_id
                )
                if is_me:
                    if not conf_word or conf_word.upper() == word.upper():
                        return True, "confirmed_me"
                    if hint and len(conf_word) == hint.length:
                        if (not hint.first or conf_word[0].upper() == hint.first.upper()) and \
                           (not hint.last or conf_word[-1].upper() == hint.last.upper()):
                            return True, "confirmed_me"

                is_other, other_word, other_player = is_message_confirmation_for_other(
                    text,
                    my_id=my_id,
                    my_username=my_uname,
                    my_first_name=my_fname,
                    my_last_name=my_lname,
                    msg_obj=m,
                    sent_msg_id=sent_msg_id
                )
                if is_other:
                    if other_word and other_word.upper() == word.upper():
                        return False, "claimed_by_other"
                    elif other_word and hint and len(other_word) == hint.length:
                        if (not hint.first or other_word[0].upper() == hint.first.upper()) and \
                           (not hint.last or other_word[-1].upper() == hint.last.upper()):
                            return False, "claimed_by_other"

                data = parse_game_bot_points_and_found(text)
                if data.get("word") and data["word"].upper() == word.upper():

                    opp_sent_first = False
                    for om in messages:
                        om_id = getattr(om, "id", 0)
                        if om_id > (game_msg_id or 0) and (sent_msg_id and om_id < sent_msg_id):
                            s_id = getattr(om, "sender_id", None)
                            if s_id and my_id and s_id != my_id:
                                if re.sub(r"[^A-Za-z0-9]", "", getattr(om, "raw_text", "") or "").strip().upper() == word.upper():
                                    opp_sent_first = True
                                    break
                    if not opp_sent_first:
                        return True, "confirmed_me"
                    else:
                        return False, "claimed_by_other"

                sender_id = getattr(m, "sender_id", None)
                if sender_id and my_id and sender_id != my_id:
                    if sent_msg_id and getattr(m, "id", 0) < sent_msg_id:
                        m_txt_clean = re.sub(r"[^A-Za-z0-9]", "", text).strip().upper()
                        if m_txt_clean == word.upper():
                            return False, "claimed_by_other"

            if game_msg_id:
                try:
                    res_g = await client.get_messages(target_gc, ids=[game_msg_id])
                    g_msgs = res_g if isinstance(res_g, (list, tuple)) else ([res_g] if res_g else [])
                    if g_msgs and g_msgs[0] and getattr(g_msgs[0], "raw_text", None):
                        g_text = g_msgs[0].raw_text
                        w_up = word.upper()
                        if f"~{w_up}~" in g_text or f"<s>{w_up}</s>" in g_text.lower():
                            for line in g_text.splitlines():
                                if w_up in line.upper():
                                    if "you" in line.lower() or (my_uname and my_uname.lower() in line.lower()) or (my_fname and my_fname.lower() in line.lower()):
                                        return True, "confirmed_me"
                                    else:
                                        return False, "claimed_by_other"
                            return True, "confirmed_me"
                except Exception:
                    pass

        except Exception as e:
            log.debug("Confirmation poll check error: %s", e)

        await asyncio.sleep(check_interval)

    return False, "timeout"

async def cross_check_and_solve_remaining(
    target_gc: int,
    orig_msg,
    raw_image: bytes,
    grid: list[list[str]],
    original_hints: list[solver.Hint],
    results: list[solver.Result],
    sent_words: list[str],
    target_user_id: int | None = None
) -> list[str]:
    """
    Cross-checks the game state after sending initial answers to GC:
    1. Pauses 2.8s to let Telegram game bot register answers and update the puzzle.
    2. Inspects latest/updated game message in GC to see which words were accepted,
       which hints remain unsolved, or if any hints were missed in the initial pass.
    3. If unsolved hints remain: re-scans the image (using OCR grid + AI vision),
       solves for the remaining hints, and sends ONLY the new missing word(s) 1-by-1 to GC.
    4. Notifies user via private Bot DM with cross-check outcome.
    """
    try:
        await asyncio.sleep(2.8)

        updated_msg = None
        if getattr(orig_msg, "id", None):
            try:
                res = await client.get_messages(target_gc, ids=[orig_msg.id])
                if res and res[0]:
                    updated_msg = res[0]
            except Exception:
                pass
        if not updated_msg:
            updated_msg = await latest_game_message(target_gc)

        updated_text = (getattr(updated_msg, "raw_text", "") or "") if updated_msg else ""

        current_hints = solver.parse_hints(updated_text) if updated_text else []
        hints_to_check = current_hints if current_hints else original_hints

        missing_hints: list[solver.Hint] = []
        remaining_sent = [w.upper() for w in sent_words]
        sent_set = set(remaining_sent)

        for h in hints_to_check:

            matched_idx = None
            for idx, w in enumerate(remaining_sent):
                if (
                    len(w) == h.length
                    and (not h.first or w[0] == h.first.upper())
                    and (not h.last or w[-1] == h.last.upper())
                ):
                    matched_idx = idx
                    break
            if matched_idx is not None:
                remaining_sent.pop(matched_idx)
                continue

            is_struck = False
            if updated_text:
                for line in updated_text.splitlines():
                    has_anchor = (h.first and h.first in line) or (h.last and h.last in line)
                    if has_anchor and ("~" in line or "found" in line.lower() or "✅" in line):
                        is_struck = True
                        break
            if not is_struck:
                missing_hints.append(h)

        if not missing_hints:
            log.info("Cross-check: All words successfully accounted for.")
            if target_user_id:
                await notifier.text(
                    "✅ <b>Cross-Check Complete</b>\nSabhi words verify ho gaye, koi missing hint nahi bacha.",
                    target_user_id=target_user_id
                )
            return []

        log.info("Cross-check: %d hints remain unsolved: %s. Re-scanning image...", len(missing_hints), missing_hints)

        rescan_results = solver.solve(grid, missing_hints)
        new_words_to_send = []
        for r in rescan_results:
            if r.best and r.best.word and r.best.word.upper() not in sent_set:
                new_words_to_send.append(r.best.word.upper())
                sent_set.add(r.best.word.upper())

        rem_set_list = list(sent_set)
        still_unsolved = []
        for h in missing_hints:
            m_idx = None
            for idx, w in enumerate(rem_set_list):
                if (
                    len(w) == h.length
                    and (not h.first or w[0] == h.first.upper())
                    and (not h.last or w[-1] == h.last.upper())
                ):
                    m_idx = idx
                    break
            if m_idx is not None:
                rem_set_list.pop(m_idx)
            else:
                still_unsolved.append(h)

        if still_unsolved and raw_image:
            hint_descriptions = []
            for h in still_unsolved:
                blanks = "_" * max(1, h.length - 2)
                pat = f"{h.first}{blanks}{h.last}" if h.last else f"{h.first}{blanks}"
                hint_descriptions.append(f"- {pat} ({h.length} letters)")

            prompt = (
                "You are an expert word search puzzle solver. Look at this puzzle grid image carefully.\n"
                "Find the exact uppercase English words in the grid matching these remaining hints:\n"
                + "\n".join(hint_descriptions) + "\n\n"
                "Return ONLY the discovered uppercase words, one per line. Do not include coordinates, arrows, numbers, or explanation."
            )
            try:
                ai_solution = await gpt_service.query_gpt(prompt=prompt, image_bytes=raw_image)
                words_found = re.findall(r"\b[A-Za-z]{2,20}\b", ai_solution)
                for w in words_found:
                    w_up = w.upper()
                    for h in still_unsolved:
                        if (
                            len(w_up) == h.length
                            and (not h.first or w_up[0] == h.first)
                            and (not h.last or w_up[-1] == h.last)
                            and w_up not in sent_set
                        ):
                            new_words_to_send.append(w_up)
                            sent_set.add(w_up)
                            break
            except Exception as e:
                log.warning("Cross-check AI vision re-scan failed: %s", e)

        if new_words_to_send:
            log.info("Cross-check: Sending %d new words to GC: %s", len(new_words_to_send), new_words_to_send)
            reply_target = getattr(updated_msg or orig_msg, "id", None)
            sent_new = await send_game_answers_to_gc(target_gc, new_words_to_send, reply_to_id=reply_target)
            if target_user_id and sent_new:
                words_fmt = "\n".join(f"• <code>{w}</code>" for w in sent_new)
                await notifier.text(
                    f"🔄 <b>Cross-Check Scan Complete</b>\nMissing words mil gaye aur GC me bhej diye:\n{words_fmt}",
                    target_user_id=target_user_id
                )
            return sent_new
        else:
            log.info("Cross-check: No additional words found after re-scan.")
            if target_user_id:
                await notifier.text(
                    "ℹ️ <b>Cross-Check Complete</b>\nRe-scan ke baad koi naya valid word nahi mila.",
                    target_user_id=target_user_id
                )
            return []

    except Exception as e:
        log.warning("Cross-check process encountered error: %s", e)
        return []

async def play_and_confirm_wordhunt_gc(
    target_gc: int,
    game_msg,
    raw_image: bytes,
    grid: list[list[str]],
    hints: list[solver.Hint],
    results: list[solver.Result],
    target_user_id: int | None = None
) -> list[str]:
    """
    Interactive Game Loop for Hidden Word Hunt in Group Chats:
    - Sends 1 word per message directly from userbot ID.
    - Waits for game bot confirmation (+3 points / You found <word>).
    - If confirmed: moves to next hint.
    - If not confirmed (timeout): searches grid for a NEW alternative word and tries it.
    - If another user answers or claimed the word/hint: SKIPS that hint immediately.
    - Returns list of words successfully sent and confirmed.
    """
    if not target_gc or not database.is_userbot_gc_reply_enabled() or not results:
        return []

    my_user_info = {"id": None, "username": None, "first_name": None, "last_name": None}
    try:
        me = await client.get_me()
        if me:
            my_user_info = {
                "id": getattr(me, "id", None),
                "username": getattr(me, "username", None),
                "first_name": getattr(me, "first_name", None),
                "last_name": getattr(me, "last_name", None)
            }
    except Exception as e:
        log.warning("Could not fetch get_me info: %s", e)

    game_text = getattr(game_msg, "raw_text", "") or ""
    already_found = extract_already_found_words(game_text)

    confirmed_words: list[str] = []
    skipped_hints: list[solver.Hint] = []
    retried_hints: list[solver.Hint] = []
    failed_hints: list[solver.Hint] = []
    used_words: set[str] = {w.upper() for w in already_found}

    try:
        from core import game_memory as _gm
        _chat = getattr(game_msg, "chat_id", None)
        _rk = _gm.current_round(_chat) if _chat is not None else None
        if _rk:
            used_words |= _gm.blocked_words(_chat, _rk)
    except Exception as _e:
        log.debug("Hidden Word Hunt: round memory unavailable: %s", _e)

    for res in results:
        hint = res.hint

        tried_words: set[str] = set(used_words)
        current_word = res.best.word if res.best and res.best.word else None

        if not current_word or current_word.upper() in tried_words:
            current_word = find_alternative_grid_word(grid, hint, tried_words, res.cands)

        hint_solved = False
        attempts = 0

        while current_word and attempts < 3 and not hint_solved:
            attempts += 1
            w_clean = re.sub(r"[^A-Za-z0-9]", "", current_word).strip().upper()
            tried_words.add(w_clean)

            try:
                res_rm = await client.get_messages(target_gc, limit=5)
                recent_msgs = res_rm if isinstance(res_rm, (list, tuple)) else ([res_rm] if res_rm else [])
                someone_claimed = False
                for rm in recent_msgs:
                    rm_text = getattr(rm, "raw_text", "") or ""
                    is_other, o_word, _ = is_message_confirmation_for_other(
                        rm_text,
                        my_user_info["id"],
                        my_user_info["username"],
                        my_user_info["first_name"],
                        my_user_info["last_name"]
                    )
                    if is_other and o_word and o_word.upper() == w_clean:
                        someone_claimed = True
                        break
                    if getattr(rm, "sender_id", None) and my_user_info["id"] and rm.sender_id != my_user_info["id"]:
                        if re.sub(r"[^A-Za-z0-9]", "", rm_text).strip().upper() == w_clean:
                            someone_claimed = True
                            break
                if someone_claimed:
                    log.info("Word '%s' was just claimed by another user before sending. Skipping hint %s!", w_clean, hint)
                    used_words.add(w_clean)
                    skipped_hints.append(hint)
                    break
            except Exception:
                pass

            sent = await send_game_answers_to_gc(target_gc, [w_clean], reply_to_id=getattr(game_msg, "id", None))
            if not sent:
                break

            sent_msg = _last_sent_gc_messages.get(target_gc)
            sent_msg_id = getattr(sent_msg, "id", None)

            confirmed, reason = await wait_for_word_confirmation(
                target_gc=target_gc,
                word=w_clean,
                hint=hint,
                my_user_info=my_user_info,
                game_msg_id=getattr(game_msg, "id", None),
                sent_msg_id=sent_msg_id,
                timeout=4.0,
                check_interval=0.3
            )

            try:
                from core import game_memory as _gm2
                _rk2 = _gm2.current_round(target_gc)
            except Exception:
                _gm2, _rk2 = None, None

            if confirmed:
                log.info("Word '%s' confirmed for hint %s (+3 points verified)! Sahi word GC me rahega.", w_clean, hint)
                confirmed_words.append(w_clean)
                used_words.add(w_clean)
                if _gm2 and _rk2:
                    _gm2.record(target_gc, _rk2, w_clean, _gm2.CONFIRMED, my_user_info.get("id"))
                hint_solved = True
                break
            elif reason == "claimed_by_other":
                log.info("Word '%s' claimed by another user. Deleting unconfirmed word & skipping hint %s!", w_clean, hint)
                await delete_wrong_gc_word(target_gc, w_clean, my_id=my_user_info.get("id"))
                used_words.add(w_clean)
                if _gm2 and _rk2:
                    _gm2.record(target_gc, _rk2, w_clean, _gm2.CONFIRMED)
                skipped_hints.append(hint)
                break
            else:

                log.info("Word '%s' was rejected (no +3 points confirmation). Deleting wrong word from GC...", w_clean)
                if _gm2 and _rk2:
                    _gm2.record(target_gc, _rk2, w_clean, _gm2.REJECTED)
                await delete_wrong_gc_word(target_gc, w_clean, my_id=my_user_info.get("id"))
                retried_hints.append(hint)
                new_word = find_alternative_grid_word(grid, hint, tried_words, res.cands)
                if new_word and new_word.upper() not in tried_words:
                    log.info("Found NEW alternative grid word '%s' for hint %s", new_word, hint)
                    current_word = new_word
                else:
                    log.info("No more alternative grid words found for hint %s.", hint)
                    break

        if not hint_solved and hint not in skipped_hints:
            failed_hints.append(hint)

    if failed_hints:
        log.info("%d hints remain unsolved. Running cross-check...", len(failed_hints))
        extra_sent = await cross_check_and_solve_remaining(
            target_gc=target_gc,
            orig_msg=game_msg,
            raw_image=raw_image,
            grid=grid,
            original_hints=failed_hints,
            results=results,
            sent_words=confirmed_words,
            target_user_id=target_user_id
        )
        if extra_sent:
            confirmed_words.extend(extra_sent)

    if target_user_id and (confirmed_words or skipped_hints or retried_hints):
        summary_lines = ["🎮 <b>Word Hunt GC Game Summary</b>\n"]
        if confirmed_words:
            words_str = ", ".join(f"<code>{w}</code>" for w in confirmed_words)
            summary_lines.append(f"• <b>Confirmed (+3 pts):</b> {len(confirmed_words)} ({words_str})")
        if retried_hints:
            summary_lines.append(f"• <b>Grid Retries (Rejected words replaced):</b> {len(retried_hints)}")
        if skipped_hints:
            summary_lines.append(f"• <b>Skipped (Opponent claimed):</b> {len(skipped_hints)}")

        await notifier.text("\n".join(summary_lines), target_user_id=target_user_id)

    return confirmed_words

async def cross_check_tenthings(
    target_gc: int,
    orig_msg,
    category: str,
    items: list[str],
    sent_answers: list[str],
    target_user_id: int | None = None
) -> list[str]:
    """
    Cross-checks 10Things game state:
    1. Pauses 2.8s.
    2. Inspects updated message in GC.
    3. If unfilled lines remain, solves and sends them 1-by-1 to GC.
    """
    try:
        await asyncio.sleep(2.8)
        updated_msg = None
        if getattr(orig_msg, "id", None):
            try:
                res = await client.get_messages(target_gc, ids=[orig_msg.id])
                if res and res[0]:
                    updated_msg = res[0]
            except Exception:
                pass
        if not updated_msg:
            updated_msg = await latest_game_message(target_gc)

        updated_text = (getattr(updated_msg, "raw_text", "") or "") if updated_msg else ""
        if not updated_text or not ten_things.is_tenthings_message(updated_text):
            return []

        new_cat, new_items = ten_things.parse_message(updated_text)
        sent_set = {a.strip().lower() for a in sent_answers}

        remaining = [item for item in new_items if "_" in item or "-" in item]
        if not remaining:
            return []

        new_answers = await ten_things.solve_10things(new_cat or category, remaining)
        to_send = [a for a in new_answers if a.strip().lower() not in sent_set]

        if to_send:
            reply_target = getattr(updated_msg or orig_msg, "id", None)
            sent = await send_game_answers_to_gc(target_gc, to_send, reply_to_id=reply_target)
            if target_user_id and sent:
                await notifier.text(
                    f"🔄 <b>10Things Cross-Check</b>\n{len(sent)} missing answer(s) bheje gaye:\n" +
                    "\n".join(f"• <code>{a}</code>" for a in sent),
                    target_user_id=target_user_id
                )
            return sent
        return []
    except Exception as e:
        log.warning("10Things cross-check failed: %s", e)
        return []

async def wait_for_new_wordly_message(
    target_gc: int,
    after_msg_id: int,
    timeout: float = 60.0
) -> Any | None:
    """Polls target_gc for a newer Wordly message with id > after_msg_id."""
    t_end = time.time() + timeout
    while time.time() < t_end:
        try:
            res = await client.get_messages(target_gc, limit=15)
            msgs = res if isinstance(res, (list, tuple)) else ([res] if res else [])
            for m in msgs:
                if m and getattr(m, "id", 0) > after_msg_id and getattr(m, "raw_text", None):
                    if wordly.is_wordly_message(m.raw_text) or wordly.is_wordly_game_over(m.raw_text):
                        return m
        except Exception as e:
            log.debug("wait_for_new_wordly_message poll error: %s", e)
        await asyncio.sleep(0.35)
    return None

async def wait_for_wordly_response(
    target_gc: int,
    word: str,
    my_user_info: dict,
    game_msg_id: int,
    sent_msg_id: int | None = None,
    timeout: float = 5.5,
    check_interval: float = 0.25
) -> tuple[bool, str, Any | None]:
    """
    Waits for Wordly bot response/confirmation in target_gc:
    - (True, "confirmed_me", new_msg) if confirmed for this userbot
    - (False, "claimed_by_other", new_msg) if confirmed for another player
    - (False, "timeout", None) if timeout elapsed without confirmation (wrong/rejected word)
    """
    t_end = time.time() + timeout
    my_id = my_user_info.get("id")
    my_uname = my_user_info.get("username")
    my_fname = my_user_info.get("first_name")
    my_lname = my_user_info.get("last_name")
    w_clean = re.sub(r"[^A-Za-z0-9]", "", word).strip().upper()

    while time.time() < t_end:
        try:
            res = await client.get_messages(target_gc, limit=15)
            msgs = res if isinstance(res, (list, tuple)) else ([res] if res else [])
            for m in msgs:
                if not m or not getattr(m, "raw_text", None):
                    continue
                text = m.raw_text

                m_id = getattr(m, "id", 0)
                if m_id > game_msg_id:

                    if wordly.is_wordly_game_over(text):
                        return False, "game_over", m

                    is_me, conf_word = wordly.is_wordly_confirmation_for_me(
                        text,
                        my_id=my_id,
                        my_username=my_uname,
                        my_first_name=my_fname,
                        my_last_name=my_lname,
                        target_word=w_clean,
                        msg_obj=m,
                        sent_msg_id=sent_msg_id
                    )
                    if is_me:
                        return True, "confirmed_me", m

                    is_other, o_word, o_player = wordly.is_wordly_confirmation_for_other(
                        text,
                        my_id=my_id,
                        my_username=my_uname,
                        my_first_name=my_fname,
                        my_last_name=my_lname,
                        target_word=w_clean,
                        msg_obj=m,
                        sent_msg_id=sent_msg_id
                    )
                    if is_other:
                        return False, "claimed_by_other", m

                sender_id = getattr(m, "sender_id", None)
                if sender_id and my_id and sender_id != my_id:
                    if sent_msg_id and getattr(m, "id", 0) < sent_msg_id:
                        if re.sub(r"[^A-Za-z0-9]", "", text).strip().upper() == w_clean:
                            return False, "claimed_by_other", None

        except Exception as e:
            log.debug("wait_for_wordly_response error: %s", e)

        await asyncio.sleep(check_interval)

    return False, "timeout", None

async def play_wordly_gc(
    target_gc: int,
    initial_msg,
    invoker_id: int | None = None
) -> None:
    """
    Continuous Game Loop for Wordly (Fill the blanks) in Group Chats:
    - Reads total word target from message (e.g. Total words found: 0/20, 5/30, 10/1000).
    - Continues playing continuously round-by-round until total target is reached or Game Over signals.
    - Sends 1 word at a time directly from userbot ID with .humanize typing delay (1s - 8s).
    - If word confirmed: STAYS permanently in GC (never deleted)!
    - If word rejected (timeout): Deleted from GC, next candidate tried.
    - If candidates for a round are exhausted: Waits up to 90s for opponents or next puzzle without quitting!
    - Strictly never sends any message to helper bot DM (notifier is bypassed).
    """
    if not target_gc or not database.is_userbot_gc_reply_enabled():
        return

    my_user_info = {"id": None, "username": None, "first_name": None, "last_name": None}
    try:
        me = await client.get_me()
        if me:
            my_user_info = {
                "id": getattr(me, "id", None),
                "username": getattr(me, "username", None),
                "first_name": getattr(me, "first_name", None),
                "last_name": getattr(me, "last_name", None),
            }
    except Exception as e:
        log.warning("Could not fetch get_me info in play_wordly_gc: %s", e)

    current_msg = initial_msg
    round_count = 0

    init_prog = wordly.extract_wordly_progress(getattr(initial_msg, "raw_text", "") or "")
    current_found = init_prog[0] if init_prog else 0
    total_target = init_prog[1] if init_prog else 1000
    log.info("Wordly: Starting continuous game loop in GC %s | Initial: %d/%d words", target_gc, current_found, total_target)

    while current_msg:
        round_count += 1
        raw_text = getattr(current_msg, "raw_text", "") or ""

        if wordly.is_wordly_game_over(raw_text):
            log.info("Wordly: Game over detected in GC %s (Game completed!)", target_gc)
            break

        prog = wordly.extract_wordly_progress(raw_text)
        if prog:
            current_found, total_target = prog
            log.info("Wordly: Progress check -> %d/%d words found", current_found, total_target)
            if total_target > 0 and current_found >= total_target:
                log.info("Wordly: All %d/%d words found! Game complete.", current_found, total_target)
                break

        data = wordly.parse_wordly_message(raw_text)
        if not data:
            log.info("Wordly: Waiting for active puzzle message in GC %s...", target_gc)
            next_m = await wait_for_new_wordly_message(target_gc, current_msg.id, timeout=30.0)
            if next_m:
                current_msg = next_m
                continue

            try:
                recent_msgs = await client.get_messages(target_gc, limit=10)
                found_puzzle = False
                for rm in (recent_msgs or []):
                    rm_text = getattr(rm, "raw_text", "") or ""
                    if rm and getattr(rm, "id", 0) > current_msg.id and wordly.is_wordly_message(rm_text):
                        current_msg = rm
                        found_puzzle = True
                        break
                if found_puzzle:
                    continue
            except Exception:
                pass
            log.info("Wordly: No new puzzle after 30s. Exiting game loop.")
            break

        letters = data["letters"]
        mask = data["mask"]
        target_len = data["target_len"]

        candidates = wordly.solve_wordly(letters, mask)
        if not candidates:
            ai_cands = await wordly.solve_wordly_with_ai_fallback(letters, mask, target_len)
            candidates = ai_cands

        if not candidates:
            log.info("Wordly: No candidates found for letters %s, mask %s. Waiting for opponent or next round (up to 90s)...", letters, mask)

            next_m = await wait_for_new_wordly_message(target_gc, current_msg.id, timeout=90.0)
            if next_m:
                current_msg = next_m
                continue
            else:
                log.info("Wordly: Inactivity timeout (90s). Exiting loop.")
                break

        round_solved = False
        for cand in candidates:
            cand_clean = re.sub(r"[^A-Za-z0-9]", "", cand).strip().title()

            try:
                res_check = await client.get_messages(target_gc, limit=5)
                recent = res_check if isinstance(res_check, (list, tuple)) else ([res_check] if res_check else [])
                newer_game_msg = None
                someone_claimed = False
                for rm in recent:
                    rm_text = getattr(rm, "raw_text", "") or ""
                    if rm and getattr(rm, "id", 0) > current_msg.id and rm_text:
                        if wordly.is_wordly_message(rm_text) or wordly.is_wordly_game_over(rm_text):
                            newer_game_msg = rm
                            break
                    if rm and getattr(rm, "sender_id", None) and my_user_info["id"] and rm.sender_id != my_user_info["id"]:
                        if re.sub(r"[^A-Za-z0-9]", "", rm_text).strip().upper() == cand_clean.upper():
                            someone_claimed = True
                            break

                if newer_game_msg:
                    log.info("Wordly: Newer puzzle detected before sending. Advancing to newer puzzle!")
                    current_msg = newer_game_msg
                    round_solved = True
                    break

                if someone_claimed:
                    log.info("Wordly: Candidate '%s' already claimed by someone else. Skipping candidate.", cand_clean)
                    continue
            except Exception:
                pass

            from core import game_memory as _gm
            _rk = _gm.current_round(target_gc)
            if _rk and cand_clean.upper() in _gm.blocked_words(target_gc, _rk):
                log.info("Wordly: '%s' already used in this round (memory). Skipping.", cand_clean)
                continue

            if database.is_humanize_enabled():
                delay = random.uniform(1.0, 8.0)
                try:
                    async with client.action(target_gc, "typing"):
                        await asyncio.sleep(delay)
                except Exception:
                    await asyncio.sleep(delay)
            else:
                await asyncio.sleep(random.uniform(0.4, 0.7))

            sent_msg = None
            try:
                cased_cand = format_game_answer_casing(cand_clean)
                sent_msg = await client.send_message(target_gc, cased_cand)
                _last_sent_gc_messages[target_gc] = sent_msg
                _rk_sent = _gm.current_round(target_gc)
                if _rk_sent:
                    _gm.record(target_gc, _rk_sent, cand_clean, _gm.CHOSEN, my_user_info.get("id"))
                log.info("Wordly: Sent candidate '%s' (cased '%s') to GC %s", cand_clean, cased_cand, target_gc)
            except Exception as e:
                log.warning("Wordly: Failed sending candidate '%s' to GC %s: %s", cand_clean, target_gc, e)
                break

            confirmed, reason, next_msg = await wait_for_wordly_response(
                target_gc=target_gc,
                word=cand_clean,
                my_user_info=my_user_info,
                game_msg_id=current_msg.id,
                sent_msg_id=getattr(sent_msg, "id", None),
                timeout=5.5,
                check_interval=0.25
            )

            if confirmed:
                log.info("Wordly: Candidate '%s' CONFIRMED! Correct word stays permanently in GC.", cand_clean)
                round_solved = True
                if next_msg:
                    current_msg = next_msg
                else:
                    current_msg = await wait_for_new_wordly_message(target_gc, current_msg.id, timeout=8.0) or current_msg
                break

            elif reason == "claimed_by_other":
                log.info("Wordly: Word claimed by another player. Deleting unconfirmed message from GC...")
                if sent_msg:
                    try: await sent_msg.delete()
                    except Exception: pass
                round_solved = True
                if next_msg:
                    current_msg = next_msg
                else:
                    current_msg = await wait_for_new_wordly_message(target_gc, current_msg.id, timeout=8.0) or current_msg
                break

            else:

                log.info("Wordly: Candidate '%s' was wrong (no bot confirmation). Deleting wrong word from GC...", cand_clean)
                if sent_msg:
                    try: await sent_msg.delete()
                    except Exception: pass

        if not round_solved:

            log.info("Wordly: Candidates exhausted for round %d. Waiting for opponent or new puzzle (up to 90s)...", round_count)
            next_m = await wait_for_new_wordly_message(target_gc, current_msg.id, timeout=90.0)
            if next_m:
                current_msg = next_m
            else:
                log.info("Wordly: Inactivity timeout (90s). Exiting loop.")
                break

def get_invoker_id(event) -> int:
    """Returns the effective user ID of the command invoker (owner or sudo user)."""
    if event.out:
        return int(config.OWNER_ID) if config.OWNER_ID else (event.sender_id or 0)
    return int(event.sender_id or config.OWNER_ID or 0)

def is_owner(event) -> bool:
    """True if message was sent by the userbot account itself or by config.OWNER_ID."""
    if event.out:
        return True
    sender_id = event.sender_id
    if not sender_id:
        return False
    return ctx_is_owner(sender_id, getattr(event, "chat_id", None))

def is_authorized(event) -> bool:
    """True if message was sent by owner or any active sudo user."""
    if is_owner(event):
        return True
    sender_id = event.sender_id
    if not sender_id:
        return False
    return ctx_is_sudo(sender_id, getattr(event, "chat_id", None))

async def reply_or_edit(event, text: str, **kwargs):
    """
    Edits the message if outgoing (userbot's own message),
    or replies to the incoming message if sent by a sudo user.
    """
    if event.out:
        try:
            return await event.edit(text, **kwargs)
        except Exception:
            return await event.reply(text, **kwargs)
    else:
        return await event.reply(text, **kwargs)

async def resolve_target_user(event, raw_arg: str | None = None):
    """
    Resolves the target user ID and entity from reply or command argument.
    raw_arg can be an ID ("12345678"), a mention ("@username"), or None.
    Returns (user_id: int | None, display_name: str, username: str | None)
    """
    entity = None
    target_id = None

    if raw_arg and raw_arg.strip():
        arg = raw_arg.strip()
        if arg.isdigit():
            target_id = int(arg)
            try:
                entity = await client.get_entity(target_id)
            except Exception:
                pass
        else:
            try:
                entity = await client.get_entity(arg)
                if entity:
                    target_id = entity.id
            except Exception:
                pass
    else:
        reply = await event.get_reply_message()
        if reply:
            target_id = reply.sender_id
            try:
                entity = await reply.get_sender()
                if not entity and target_id:
                    entity = await client.get_entity(target_id)
            except Exception:
                pass

    first = getattr(entity, 'first_name', '') or ""
    last = getattr(entity, 'last_name', '') or ""
    full_name = f"{first} {last}".strip()
    uname = getattr(entity, 'username', None)

    return target_id, full_name or "User", uname

@client.on(events.NewMessage(pattern=r"^[./]stop(?:\s+|$)"))
async def on_stop(event):
    if not is_authorized(event):
        return

    queued_cleared = 0
    for q in _chat_queues.values():
        while not q.empty():
            try:
                q.get_nowait()
                q.task_done()
                queued_cleared += 1
            except Exception:
                break

    cancelled = 0
    for chat_id, tasks in list(_active_tasks.items()):
        for t in tasks:
            if not t.done():
                t.cancel()
                cancelled += 1
        _active_tasks[chat_id] = []

    busy_count = len(busy)
    busy.clear()
    _grab_debounce.clear()
    _user_debounce.clear()

    if cancelled or queued_cleared or busy_count:
        summary = (
            f"🛑 <b>Stop:</b> {cancelled} task cancel, {queued_cleared} queued kaam hata diya, "
            f"{busy_count} busy chat reset."
        )
    else:
        summary = "i️ <i>Koi task chal nahi raha hai.</i>"
    await reply_or_edit(event, summary, parse_mode="html")

@client.on(events.NewMessage(pattern=config.TRIGGER))
async def on_trigger(event):
    if not is_authorized(event):
        return

    chat_id = event.chat_id

    enqueue_heavy_task(chat_id, _process_trigger(event, chat_id))

async def _process_trigger(event, chat_id):
    reply_msg = None
    try:
        reply = await event.get_reply_message()
        invoker_id = get_invoker_id(event)
        target_gc = event.chat_id if event.is_group and (database.is_auth_gc_for_user(event.chat_id, invoker_id) or is_owner(event) or database.is_auth_gc(event.chat_id) or not database.get_auth_gc_ids()) else None

        if event.is_group:
            try: await event.delete()
            except Exception: pass
        else:
            reply_msg = await reply_or_edit(event, "⏳ Processing...")

        msg = reply or await latest_game_message(chat_id)
        if not msg:
            err = "❌ Game ka message nahi mila. Photo ya text message par reply karke <code>.k</code> likho."
            if reply_msg:
                try: await reply_msg.edit(err, parse_mode="html")
                except Exception: pass
            await notifier.text(err, target_user_id=invoker_id)
            return

        if wordly.is_wordly_message(msg.raw_text):
            data = wordly.parse_wordly_message(msg.raw_text)
            cands = wordly.solve_wordly(data["letters"], data["mask"]) if data else []
            best = cands[0] if cands else None

            if reply_msg:
                sol_text = "🧩 <b>Wordly Solution</b>\n"
                if data:
                    sol_text += f"Letters: <code>{' '.join(data['letters'])}</code>\nMask: <code>{data['mask']}</code>\n\n"
                sol_text += f"✅ <b>{best or 'Not found'}</b>"
                if len(cands) > 1:
                    sol_text += f"\n<i>Alternatives: {', '.join(cands[1:5])}</i>"
                try: await reply_msg.edit(sol_text, parse_mode="html")
                except Exception:
                    try: await reply_msg.reply(sol_text, parse_mode="html")
                    except Exception: pass

            if target_gc and database.is_userbot_gc_reply_enabled():
                await play_wordly_gc(target_gc, msg, invoker_id=invoker_id)
            return

        elif ten_things.is_tenthings_message(msg.raw_text):
            cat, items = ten_things.parse_message(msg.raw_text)
            if not cat:
                err = "❌ 10Things hints parse nahi ho paaye."
                if reply_msg:
                    try: await reply_msg.edit(err, parse_mode="html")
                    except Exception: pass
                await notifier.text(err, target_user_id=invoker_id)
                return

            try:
                from core import game_memory as _gm3

                _rk3 = _gm3.current_round(msg.chat_id) or f"tt:{msg.id}"
                _gm3.start_round(msg.chat_id, _rk3)
                _blocked3 = _gm3.blocked_words(msg.chat_id, _rk3)
            except Exception:
                _gm3, _rk3, _blocked3 = None, None, set()
            answers = await ten_things.solve_10things(cat, items, blocked=_blocked3)
            if answers and _gm3 and _rk3:
                for _a in answers:
                    if _a:
                        _gm3.record(msg.chat_id, _rk3, _a, _gm3.CHOSEN, None)
            if answers:
                report = f"🎯 <b>10Things Answers</b>\n<i>{cat}</i>\n\n"
                for i, ans in enumerate(answers, 1):
                    report += f"{i}. <code>{ans}</code>\n"
                if reply_msg:
                    try: await reply_msg.edit(report, parse_mode="html")
                    except Exception: pass

                if target_gc and database.is_userbot_gc_reply_enabled():
                    sent_answers = await send_game_answers_to_gc(target_gc, answers, reply_to_id=getattr(msg, "id", None))
                    await cross_check_tenthings(target_gc, msg, cat, items, sent_answers, target_user_id=invoker_id)

                await notifier.text(report, target_user_id=invoker_id)
            else:
                err = "⚠️ 10Things ke answers nahi mil paaye."
                if reply_msg:
                    try: await reply_msg.edit(err, parse_mode="html")
                    except Exception: pass
                await notifier.text(err, target_user_id=invoker_id)

        elif msg.media and msg.raw_text and solver.parse_hints(msg.raw_text):

            try:
                await asyncio.wait_for(
                    solve_message(msg, extra_chat_id=target_gc, reply_msg=reply_msg, target_user_id=invoker_id),
                    timeout=300.0
                )
            except asyncio.TimeoutError:
                err = "⏱ Timeout: solve 5 minutes mein complete nahi hua."
                log.warning("solve_message timed out for chat %s", chat_id)
                if reply_msg:
                    try: await reply_msg.edit(err, parse_mode="html")
                    except Exception: pass
                try: await notifier.text(err, target_user_id=invoker_id)
                except Exception: pass

        else:
            log.info("Detected other game type. Using Universal AI Game Solver...")
            t0 = time.time()
            media_bytes = None
            if msg.media:
                media_bytes = await client.download_media(msg, file=bytes)

            try:
                from core import game_memory as _gm4
                _rk4 = f"ai:{msg.id}"
                _gm4.start_round(msg.chat_id, _rk4)
                _blocked4 = _gm4.blocked_words(msg.chat_id, _rk4)
            except Exception:
                _gm4, _rk4, _blocked4 = None, None, set()
            solution = await gpt_service.solve_game_ai(
                game_text=msg.raw_text or "", image_bytes=media_bytes, blocked=_blocked4)
            if _gm4 and _rk4 and solution:

                for _line in solution.splitlines():
                    _m4 = re.match(r"^\s*\d+\s*[.):\-]\s*([A-Za-z][A-Za-z ]{1,24})\s*$", _line)
                    if _m4:
                        _gm4.record(msg.chat_id, _rk4, _m4.group(1).strip(), _gm4.CHOSEN, None)
            secs = time.time() - t0
            report = f"{solution}\n\n⏱ {secs:.1f}s"
            if reply_msg:
                try: await reply_msg.edit(report, parse_mode="html")
                except Exception: pass

            if target_gc and database.is_userbot_gc_reply_enabled():
                bare_words = extract_bare_game_answers(solution)
                if bare_words:
                    await send_game_answers_to_gc(target_gc, bare_words, reply_to_id=getattr(msg, "id", None))

            await notifier.text(report, target_user_id=invoker_id)

    except asyncio.CancelledError:
        if reply_msg:
            try: await reply_msg.edit("🛑 <b>Task Stopped.</b>", parse_mode="html")
            except Exception: pass
        raise
    except Exception as e:
        log.exception("solve failed")
        err = f"⚠️ Error: <code>{e}</code>"
        if reply_msg:
            try: await reply_msg.edit(err, parse_mode="html")
            except Exception: pass
        try:
            await notifier.text(err, target_user_id=invoker_id)
        except Exception:
            pass

@client.on(events.MessageEdited(incoming=True, func=lambda e: e.is_group))
async def on_game_message_edited(event):
    """/hint reveals a letter by editing the puzzle message in place.
    Same round (same message id), new mask: memory is kept, the solver reads the newest text."""
    try:
        from core import game_memory
        text = getattr(event, "raw_text", "") or ""
        if ten_things.is_tenthings_message(text):
            rk = f"tt:{event.id}"
            if game_memory.current_round(event.chat_id) == rk:
                log.info("10Things hint edit detected in chat %s (round kept, mask refreshed).", event.chat_id)
            else:
                game_memory.start_round(event.chat_id, rk)
    except Exception as e:
        log.debug("on_game_message_edited skipped: %s", e)

@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_group))
async def feed_game_memory(event):
    """Records every group message into the chat buffer and updates Octopus round memory.
    Runs independently of the auto-play handler below; never raises."""
    try:
        from core import chat_buffer, game_memory, octopus
        chat_id = event.chat_id
        text = getattr(event, "raw_text", "") or ""
        sender_id = getattr(event, "sender_id", None)
        sender = await event.get_sender() if sender_id else None
        name = (getattr(sender, "first_name", "") or "") + (
            f" {sender.last_name}" if getattr(sender, "last_name", None) else "")
        chat_buffer.add(chat_id, event.id, sender_id, name.strip(), text)

        if octopus.is_octopus_message(text):
            st = octopus.parse_state(text)
            if st:
                game_memory.start_round(chat_id, st["round_key"])
                game_memory.set_round_meta(chat_id, st["round_key"], st["mask"], st["length"], event.id)
        elif ten_things.is_tenthings_message(text):

            game_memory.start_round(chat_id, f"tt:{event.id}")
        elif re.search(r"\bgot\s+[\"']?[A-Za-z]{2,25}[\"']?\s*$", text, re.I | re.M) and game_memory.current_round(chat_id):

            m_res = re.search(r"^(?P<name>.+?)\s+got\s+[\"']?(?P<word>[A-Za-z]{2,25})[\"']?\s*$", text.strip(), re.I | re.M)
            if m_res:
                pid_res = chat_buffer.find_sender_id(chat_id, m_res.group("name").strip())
                game_memory.record(chat_id, game_memory.current_round(chat_id),
                                   m_res.group("word"), game_memory.CONFIRMED, pid_res)
        elif octopus.is_confirmation(text):
            info = octopus.parse_confirmation(text)
            rk = game_memory.current_round(chat_id)
            if info and rk:
                meta = game_memory.round_meta(chat_id, rk)
                pid = chat_buffer.find_sender_id(chat_id, info["name"])
                word = None
                if meta:
                    word = octopus.find_answer_word(
                        chat_id, pid, meta["mask"], meta["length"], meta["start_msg_id"])
                if word:
                    game_memory.record(chat_id, rk, word, game_memory.CONFIRMED, pid)
                else:
                    log.info("Octopus: confirmation for %s but submitted word not found in buffer.", info["name"])
    except Exception as e:
        log.debug("feed_game_memory skipped: %s", e)

@client.on(events.NewMessage(incoming=True, func=lambda e: e.is_group))
async def on_wordly_auto(event):
    """
    Auto-detects Wordly game messages in authorized group chats when .gcreply is enabled.
    Enters interactive playing loop automatically without requiring manual .k trigger.
    """
    if not event.is_group:
        return
    chat_id = event.chat_id
    if not database.is_userbot_gc_reply_enabled():
        return
    auth_ids = database.get_auth_gc_ids()
    if auth_ids and chat_id not in auth_ids:
        return
    if chat_id in busy:
        return

    raw_text = getattr(event, "raw_text", "") or ""
    if not raw_text or not wordly.is_wordly_message(raw_text):
        return

    now = time.monotonic()
    if chat_id in _trigger_debounce and now - _trigger_debounce[chat_id] < 2.5:
        return
    _trigger_debounce[chat_id] = now
    busy.add(chat_id)
    try:
        await play_wordly_gc(chat_id, event.message)
    except Exception as e:
        log.warning("on_wordly_auto error in GC %s: %s", chat_id, e)
    finally:
        busy.discard(chat_id)

@client.on(events.NewMessage(pattern=r"^\.sudo(?:\s+(.*))?$"))
async def sudo_cmd(event):
    """Owner only: Grants sudo rights so a user can execute commands from userbot ID."""
    if not is_owner(event):
        if is_authorized(event):
            await reply_or_edit(event, "⚠️ <i>Sirf Bot Owner naye sudo users bana sakte hain.</i>", parse_mode="html")
        return

    arg = event.pattern_match.group(1)
    target_id, full_name, uname = await resolve_target_user(event, arg)

    if not target_id:
        await reply_or_edit(
            event,
            "❌ <b>Kisko SUDO banana hai?</b>\nMessage par reply karein ya user ID / @username dein:\n<code>.sudo 12345678</code> ya <code>.sudo @username</code>",
            parse_mode="html"
        )
        return

    database.add_sudo_user(target_id)
    u_tag = f"(@{uname})" if uname else ""
    await reply_or_edit(
        event,
        f"🛡️ <b>SUDO Granted!</b>\n"
        f"• <b>User:</b> {full_name} {u_tag}\n"
        f"• <b>ID:</b> <code>{target_id}</code>\n\n"
        f"✅ <i>Ab ye user aapki ID se commands (.k, .c, .gpt, .kang, .g, etc.) use kar sakta hai.</i>",
        parse_mode="html"
    )

@client.on(events.NewMessage(pattern=r"^\.unsudo(?:\s+(.*))?$"))
async def unsudo_cmd(event):
    """Owner only: Revokes sudo rights from a user."""
    if not is_owner(event):
        if is_authorized(event):
            await reply_or_edit(event, "⚠️ <i>Sirf Bot Owner sudo users hata sakte hain.</i>", parse_mode="html")
        return

    arg = event.pattern_match.group(1)
    target_id, full_name, uname = await resolve_target_user(event, arg)

    if not target_id:
        await reply_or_edit(
            event,
            "❌ <b>Kisko UNSUDO karna hai?</b>\nMessage par reply karein ya user ID / @username dein:\n<code>.unsudo 12345678</code> ya <code>.unsudo @username</code>",
            parse_mode="html"
        )
        return

    database.remove_sudo_user(target_id)
    u_tag = f"(@{uname})" if uname else ""
    await reply_or_edit(
        event,
        f"❌ <b>SUDO Revoked!</b>\n"
        f"• <b>User:</b> {full_name} {u_tag}\n"
        f"• <b>ID:</b> <code>{target_id}</code>\n\n"
        f"🚫 <i>Sudo access successfully remove kar diya gaya hai.</i>",
        parse_mode="html"
    )

@client.on(events.NewMessage(pattern=r"^\.sudousers$"))
async def sudousers_cmd(event):
    """Lists all sudo users with Name, Username, and ID."""
    if not is_authorized(event):
        return

    users = database.get_sudo_users()
    if not users:
        await reply_or_edit(event, "ℹ️ Koi bhi sudo user added nahi hai.")
        return

    lines = [f"🛡️ <b>Sudo Users ({len(users)}):</b>\n"]
    for uid in sorted(users):
        name_str = ""
        try:
            entity = await client.get_entity(uid)
            if entity:
                first = getattr(entity, 'first_name', '') or ""
                last = getattr(entity, 'last_name', '') or ""
                full_name = f"{first} {last}".strip() or "User"
                uname = f"(@{entity.username})" if getattr(entity, 'username', None) else ""
                tag = f"{full_name} {uname}".strip()
                if tag:
                    name_str = f" - <b>{tag}</b>"
        except Exception:
            pass
        lines.append(f"• <code>{uid}</code>{name_str}")

    await reply_or_edit(event, "\n".join(lines), parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:promote|auth)(?:\s+(.*))?$"))
async def promote_cmd(event):
    """Owner & Sudo: Promotes/authorizes a user to receive answers in DM."""
    if not is_authorized(event):
        return
    arg = event.pattern_match.group(1)
    target_id, full_name, uname = await resolve_target_user(event, arg)
    if not target_id:
        await reply_or_edit(event, "❌ Kisko auth karu? Message par reply karo ya ID/@username do.")
        return
    database.add_promoted_user(target_id)
    u_tag = f"(@{uname})" if uname else ""
    await reply_or_edit(event, f"✅ User <b>{full_name}</b> {u_tag} (<code>{target_id}</code>) promoted successfully!", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:demote|unauth|unuth)(?:\s+(.*))?$"))
async def demote_cmd(event):
    """Owner & Sudo: Demotes/unauthorizes a user."""
    if not is_authorized(event):
        return
    arg = event.pattern_match.group(1)
    target_id, full_name, uname = await resolve_target_user(event, arg)
    if not target_id:
        await reply_or_edit(event, "❌ Kisko unauth karu? Message par reply karo ya ID/@username do.")
        return
    database.remove_promoted_user(target_id)
    u_tag = f"(@{uname})" if uname else ""
    await reply_or_edit(event, f"❌ User <b>{full_name}</b> {u_tag} (<code>{target_id}</code>) demoted successfully!", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:users|authed|promoted)$"))
async def users_cmd(event):
    """
    Lists all promoted/authorized users with their user IDs and names.
    """
    if not is_authorized(event):
        return

    users = database.get_promoted_users()
    if not users:
        await reply_or_edit(event, "ℹ️ Koi bhi user promoted/authorized nahi hai.")
        return

    lines = [f"👥 <b>Promoted / Authorized Users ({len(users)}):</b>\n"]
    for uid in sorted(users):
        name_str = ""
        try:
            entity = await client.get_entity(uid)
            if entity:
                first = getattr(entity, 'first_name', '') or ""
                last = getattr(entity, 'last_name', '') or ""
                full_name = f"{first} {last}".strip()
                uname = f"(@{entity.username})" if getattr(entity, 'username', None) else ""
                tag = f"{full_name} {uname}".strip()
                if tag:
                    name_str = f" - <b>{tag}</b>"
        except Exception:
            pass
        lines.append(f"• <code>{uid}</code>{name_str}")

    await reply_or_edit(event, "\n".join(lines), parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:gcauth|authgc)(?:\s+(.*))?$"))
async def gcauth_cmd(event):
    """Owner/Sudo: Authorizes a group chat so bot answers/solutions are delivered to this GC for the invoking user."""
    if not is_authorized(event):
        return

    arg = (event.pattern_match.group(1) or "").strip()
    target_chat_id = None
    chat_title = ""

    if arg and (arg.startswith("-") or arg.isdigit()):
        try:
            target_chat_id = int(arg)
            chat_title = f"GC {target_chat_id}"
        except ValueError:
            pass
    elif event.is_group:
        target_chat_id = event.chat_id
        try:
            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', None) or "Group Chat"
        except Exception:
            chat_title = "Group Chat"

    if not target_chat_id:
        await reply_or_edit(
            event,
            "❌ <b>Group Chat ID nahi mili!</b>\n"
            "Ye command kisi group mein chalayein ya group ID dein:\n"
            "<code>.gcauth</code> (group ke andar) ya <code>.gcauth -1001234567890</code>",
            parse_mode="html"
        )
        return

    invoker_id = get_invoker_id(event)
    database.add_auth_gc(target_chat_id, chat_title, user_id=invoker_id)
    await reply_or_edit(
        event,
        f"✅ <b>Group Chat Authorized for User <code>{invoker_id}</code>!</b>\n"
        f"• <b>Title:</b> {chat_title}\n"
        f"• <b>Chat ID:</b> <code>{target_chat_id}</code>\n\n"
        f"<i>Ab is group mein aapke triggers (.k/.c) ke answers bot deliver karega.</i>",
        parse_mode="html"
    )

    try:
        await notifier.send_to_chat(
            target_chat_id,
            f"🤖 <b>Bot Authorized in this Group for User <code>{invoker_id}</code>!</b>\n"
            f"Ab is group mein aapke <code>.k</code> game solutions deliver honge."
        )
    except Exception:
        pass

@client.on(events.NewMessage(pattern=r"^\.(?:gcunauth|unauthgc)(?:\s+(.*))?$"))
async def gcunauth_cmd(event):
    """Owner/Sudo: Revokes authorization from a group chat for the invoking user."""
    if not is_authorized(event):
        return

    arg = (event.pattern_match.group(1) or "").strip()
    target_chat_id = None
    if arg and (arg.startswith("-") or arg.isdigit()):
        try:
            target_chat_id = int(arg)
        except ValueError:
            pass
    elif event.is_group:
        target_chat_id = event.chat_id

    if not target_chat_id:
        await reply_or_edit(event, "❌ Group ID provide karein ya group mein <code>.gcunauth</code> likhein.", parse_mode="html")
        return

    invoker_id = get_invoker_id(event)
    database.remove_auth_gc(target_chat_id, user_id=invoker_id)
    await reply_or_edit(
        event,
        f"❌ <b>Group Chat Unauthenticated for User <code>{invoker_id}</code>!</b>\n• <b>Chat ID:</b> <code>{target_chat_id}</code>\n<i>Ab is GC mein aapke triggers ke solutions nahi aayenge.</i>",
        parse_mode="html"
    )

@client.on(events.NewMessage(pattern=r"^\.(?:gclist|gcauths)$"))
async def gclist_cmd(event):
    """Owner/Sudo: Lists all authorized group chats."""
    if not is_authorized(event):
        return

    invoker_id = get_invoker_id(event)
    gcs = database.get_auth_gcs()
    if not gcs:
        await reply_or_edit(event, "ℹ️ Koi bhi Group Chat authorized nahi hai.")
        return

    lines = [f"👥 <b>Authorized Group Chats ({len(gcs)}):</b>\n"]
    for gid, title in gcs.items():
        t_str = f" - <b>{title}</b>" if title else ""
        users = database.get_gc_auth_users(gid)
        u_str = ", ".join(f"<code>{u}</code>" for u in users) if users else "None"
        active_tag = " (✅ Active for you)" if invoker_id in users else ""
        lines.append(f"• <code>{gid}</code>{t_str}\n  └ Users: {u_str}{active_tag}")

    await reply_or_edit(event, "\n".join(lines), parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.yaad(?:\s+([\s\S]+))?$"))
async def yaad_cmd(event):
    """
    Saves a text fact or replied message into bot's persistent memory.
    """
    if not is_authorized(event):
        return

    text_arg = event.pattern_match.group(1)
    content_to_save = (text_arg or "").strip()

    if not content_to_save:
        reply = await event.get_reply_message()
        if reply and (reply.raw_text or reply.message):
            content_to_save = (reply.raw_text or reply.message).strip()

    if not content_to_save:

        mems = database.get_memories()
        if not mems:
            await reply_or_edit(event, "🧠 <i>Abhi tak koi memory save nahi hai. Kuch yaad karwane ke liye likhein:</i> <code>.yaad &lt;baat&gt;</code>", parse_mode="html")
        else:
            lines = [f"🧠 <b>Bot Ki Memory ({len(mems)} Saved Facts):</b>\n"]
            for m in mems:
                lines.append(f"• <code>#{m['id']}</code>: <i>{m['content']}</i>")
            lines.append("\nDelete: <code>.bhool &lt;id ya text&gt;</code>")
            await reply_or_edit(event, "\n".join(lines), parse_mode="html")
        return

    mem = database.add_memory(content_to_save)
    resp = (
        f"🧠 <b>Yaad rakh liya!</b>\n"
        f"<i>\"{content_to_save}\"</i>\n\n"
        f"📌 <b>Memory ID:</b> <code>#{mem['id']}</code>\n"
        f"✨ <i>Ab se <code>.gpt</code> mein poochne par bot ise yaad rakhega.</i>"
    )
    await reply_or_edit(event, resp, parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:yaadein|memories)$"))
async def yaadein_cmd(event):
    """
    Lists all memories saved in the bot.
    """
    if not is_authorized(event):
        return

    mems = database.get_memories()
    if not mems:
        await reply_or_edit(event, "🧠 <i>Abhi tak koi memory save nahi hai. Kuch yaad karwane ke liye likhein:</i> <code>.yaad &lt;text&gt;</code>", parse_mode="html")
        return

    lines = [f"🧠 <b>Bot Ki Memory ({len(mems)} Saved Facts):</b>\n"]
    for m in mems:
        lines.append(f"• <code>#{m['id']}</code>: <i>{m['content']}</i>")
    lines.append("\nDelete: <code>.bhool &lt;id ya text&gt;</code>")
    await reply_or_edit(event, "\n".join(lines), parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:bhool|forget|bhoolja)(?:\s+([\s\S]+))?$"))
async def bhool_cmd(event):
    """
    Deletes a memory by ID, matching text, or all.
    """
    if not is_authorized(event):
        return

    query = event.pattern_match.group(1)
    if not query or not query.strip():
        await reply_or_edit(event, "❌ <i>Kripya memory ID ya text likhein jo bhoolna hai:</i> <code>.bhool &lt;id/text&gt;</code> <i>ya</i> <code>.bhool all</code>", parse_mode="html")
        return

    query = query.strip()
    if query.lower() in ("all", "sab", "puri"):
        count = database.clear_all_memories()
        await reply_or_edit(event, f"🗑️ <b>Sabhi yaadein bhool gaya!</b> (Total {count} memories cleared) ✅", parse_mode="html")
        return

    clean_q = query.lstrip("#")
    ok, deleted = database.delete_memory(clean_q)
    if ok:
        await reply_or_edit(event, f"🗑️ <b>Yaad bhool gaya:</b>\n<i>\"{deleted}\"</i>\n\n✅ <i>Memory se permanently delete kar diya gaya.</i>", parse_mode="html")
    else:
        await reply_or_edit(event, "❌ <i>Matching memory nahi mili. <code>.yaadein</code> check karein.</i>", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:on|autoreply\s+on)$"))
async def autoreply_on_cmd(event):
    """Enables Telegram Bot auto-reply mode."""
    if not is_authorized(event):
        return
    database.set_auto_reply_enabled(True)
    await reply_or_edit(
        event,
        "🤖 <b>Bot Auto-Reply:</b> <code>ON</code> ✅\n\n<i>Ab Telegram bot aane wale sabhi messages par automatically AI reply dega.</i>",
        parse_mode="html"
    )

@client.on(events.NewMessage(pattern=r"^\.(?:off|autoreply\s+off)$"))
async def autoreply_off_cmd(event):
    """Disables Telegram Bot auto-reply mode (silent mode)."""
    if not is_authorized(event):
        return
    database.set_auto_reply_enabled(False)
    await reply_or_edit(
        event,
        "🔇 <b>Bot Auto-Reply:</b> <code>OFF</code> ⏸️\n\n<i>Ab Telegram bot sirf explicit commands (.gpt, .k, .c, games) par reply karega.</i>",
        parse_mode="html"
    )

ALL_COMMANDS_HELP = help_menu.get_all_commands_text()

@client.on(events.NewMessage(pattern=r"^\.(?:start|alive)$"))
async def start_cmd(event):
    """Intro and developer information."""
    if not is_authorized(event):
        return

    user_name = "User"
    try:
        sender = await event.get_sender()
        if sender:
            fn = getattr(sender, "first_name", "") or ""
            ln = getattr(sender, "last_name", "") or ""
            full = f"{fn} {ln}".strip()
            user_name = full or getattr(sender, "username", "") or "User"
        elif event.out:
            me = await client.get_me()
            if me:
                fn = getattr(me, "first_name", "") or ""
                ln = getattr(me, "last_name", "") or ""
                full = f"{fn} {ln}".strip()
                user_name = full or getattr(me, "username", "") or "User"
    except Exception:
        pass

    safe_name = html.escape(user_name)
    uptime = get_readable_uptime()
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    start_text = (
        f"╭━━━━〔 ⚡ <b>VIPER USERBOT</b> ⚡ 〕━━━━╮\n"
        f"┃\n"
        f"┃ 👑 <b>Master:</b> <code>{safe_name}</code>\n"
        f"┃ 🟢 <b>Status:</b> <code>Supercharged & Online</code>\n"
        f"┃ ⏱️ <b>Uptime:</b> <code>{uptime}</code>\n"
        f"┃ 🐍 <b>Version:</b> <code>v2.5.0 Premium</code>\n"
        f"┃ ⚙️ <b>Engine:</b> <code>Python {py_ver} • Telethon</code>\n"
        f"┃ 🎮 <b>Solvers:</b> <code>Wordly • 10Things • Word Hunt</code>\n"
        f"┃ 🤖 <b>AI Core:</b> <code>Online (.gpt)</code>\n"
        f"┃ 👨‍💻 <b>Developer:</b> @Gazarkahalwahu\n"
        f"┃\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"✨ <i>The ultimate, lightning-fast Telegram userbot equipped with automated Game Solvers, multi-model AI, media grabbers, and stealth utilities.</i>\n\n"
        f"🧭 <b>Quick Commands:</b>\n"
        f"• <code>.help</code> ➔ <i>Interactive Category Help Menu</i>\n"
        f"• <code>.ping</code> ➔ <i>Measure Real-time Latency</i>\n"
        f"• <code>.repo</code> ➔ <i>GitHub Repo & 1-Click Deploy</i>\n"
        f"• <code>.update</code> ➔ <i>Auto-Pull Latest Updates</i>"
    )
    await reply_or_edit(event, start_text, parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.ping$"))
async def ping_cmd(event):
    """Measures accurate round-trip ping latency in milliseconds."""
    if not is_authorized(event):
        return

    user_name = "User"
    try:
        sender = await event.get_sender()
        if sender:
            fn = getattr(sender, "first_name", "") or ""
            ln = getattr(sender, "last_name", "") or ""
            full = f"{fn} {ln}".strip()
            user_name = full or getattr(sender, "username", "") or "User"
        elif event.out:
            me = await client.get_me()
            if me:
                fn = getattr(me, "first_name", "") or ""
                ln = getattr(me, "last_name", "") or ""
                full = f"{fn} {ln}".strip()
                user_name = full or getattr(me, "username", "") or "User"
    except Exception:
        pass

    safe_name = html.escape(user_name)

    t0 = time.time()
    msg = await reply_or_edit(event, "📡 <i>Pinging Telegram servers...</i>", parse_mode="html")
    latency = (time.time() - t0) * 1000

    if latency < 80:
        speed_badge = "🚀 Blazing Fast"
    elif latency < 180:
        speed_badge = "⚡ Lightning Fast"
    elif latency < 350:
        speed_badge = "🏎️ Smooth"
    else:
        speed_badge = "🐢 Normal"

    uptime = get_readable_uptime()

    ping_text = (
        f"╭━━━━〔 🏓 <b>VIPER PING</b> 〕━━━━╮\n"
        f"┃\n"
        f"┃ ⚡ <b>Latency:</b> <code>{latency:.2f} ms</code> [{speed_badge}]\n"
        f"┃ ⏱️ <b>Uptime:</b> <code>{uptime}</code>\n"
        f"┃ 🟢 <b>State:</b> <code>Fully Operational</code>\n"
        f"┃ 👤 <b>Master:</b> <code>{safe_name}</code>\n"
        f"┃ 👨‍💻 <b>Developer:</b> @Gazarkahalwahu\n"
        f"┃\n"
        f"╰━━━━━━━━━━━━━━━━━━━━━━━╯"
    )
    await msg.edit(ping_text, parse_mode="html")

@client.on(events.NewMessage(pattern=r"^[,.]repo$"))
async def repo_cmd(event):
    """
    Displays official Viper Userbot repository link, deployment guides, and developer credits.
    """
    if not is_authorized(event):
        return

    repo_url = getattr(config, "UPSTREAM_REPO", "") or "https://github.com/Viperr-userbot/Viper-Userbot"
    clean_repo_url = repo_url[:-4] if repo_url.endswith(".git") else repo_url
    branch = getattr(config, "UPSTREAM_BRANCH", "main")

    card = (
        "🐍 <b>VIPER USERBOT — OFFICIAL REPOSITORY</b> 🐍\n"
        "────────────────────────\n"
        "⭐ <b>GitHub Repository:</b>\n"
        f"👉 <a href=\"{clean_repo_url}\">{clean_repo_url}</a>\n\n"
        "🚀 <b>1-Click Deploy (24/7 Free):</b>\n"
        "👉 <a href=\"https://app.getvoroa.com/\">Deploy on Voroa Cloud</a>\n\n"
        "✨ <b>Core Features:</b>\n"
        "• 🎮 <i>Game Solvers (Wordly, Word Hunt, 10Things, Octopus)</i>\n"
        "• 🤖 <i>Multi-Engine AI Assistant (.gpt)</i>\n"
        "• 🎵 <i>320kbps Music Downloader (.song)</i>\n"
        "• 🛡️ <i>100% Anti-Ban Human Typing Simulator</i>\n\n"
        f"🌿 <b>Branch:</b> <code>{branch}</code>\n"
        "👨‍💻 <b>Developer:</b> @Gazarkahalwahu\n"
        "────────────────────────\n"
        "💡 <i>Star ⭐ & Fork 🍴 the repo on GitHub to support!</i>"
    )
    await reply_or_edit(event, card, parse_mode="html", link_preview=False)

@client.on(events.NewMessage(pattern=r"^[,.]restart$"))
async def restart_cmd(event):
    """
    Fully restarts the userbot process.
    RESTRICTED TO BOT OWNER ONLY (Sudo users are not permitted).
    """
    if not is_owner(event):
        if is_authorized(event):
            await reply_or_edit(
                event,
                "⛔ <b>Permission Denied:</b> Sirf bot owner hi restart kar sakte hain (sudo users are not permitted).",
                parse_mode="html"
            )
        return

    chat_id = event.chat_id
    msg = await reply_or_edit(event, "🔄 <b>Restarting userbot...</b>", parse_mode="html")
    msg_id = getattr(msg, "id", None) or getattr(event, "id", None)

    database.set_restart_state(chat_id, msg_id or "")

    log.info("Full restart initiated by owner in chat %s. Restarting process...", chat_id)
    await asyncio.sleep(0.8)

    try:
        await client.disconnect()
    except Exception:
        pass

    args = [sys.executable] + (sys.argv if sys.argv else ["main.py"])
    try:
        os.execv(sys.executable, args)
    except Exception as e:
        log.error("os.execv failed: %s. Using sys.exit(0)...", e)
        sys.exit(0)

def find_git_binary() -> str | None:
    """Finds available git executable in PATH or standard system directories."""
    git_bin = shutil.which("git")
    if git_bin:
        return git_bin
    common_paths = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
        "/usr/bin/git",
        "/usr/local/bin/git",
        "/bin/git",
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p
    return None

async def perform_http_zip_update(event, only_check: bool = False, force: bool = False):
    """
    Direct GitHub HTTP/Zip updater fallback when git CLI is not installed
    or when repository was deployed without .git.
    Extracts updates while strictly preserving sensitive user data (.env, sessions, db, etc.).
    """
    repo_url = getattr(config, "UPSTREAM_REPO", "") or "https://github.com/Viperr-userbot/Viper-Userbot"
    branch = getattr(config, "UPSTREAM_BRANCH", "") or "main"

    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?$", repo_url)
    if m:
        owner, repo_name = m.group(1), m.group(2)
    else:
        owner, repo_name = "Viperr-userbot", "Viper-Userbot"

    headers = {
        "User-Agent": "Viper-Userbot/1.0",
        "Accept": "application/vnd.github.v3+json"
    }

    commit_sha = ""
    commit_msg = "Latest upstream release"
    commit_author = "Developer"

    status_msg = await reply_or_edit(
        event,
        f"🔍 <b>Checking updates directly from GitHub ({owner}/{repo_name}@{branch})...</b>",
        parse_mode="html"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client_http:
            api_url = f"https://api.github.com/repos/{owner}/{repo_name}/commits/{branch}"
            resp = await client_http.get(api_url, headers=headers)
            if resp.status_code == 200:
                c_data = resp.json()
                commit_sha = c_data.get("sha", "")
                commit_msg = c_data.get("commit", {}).get("message", "Latest upstream update").splitlines()[0]
                commit_author = c_data.get("commit", {}).get("author", {}).get("name", "Developer")
            else:
                log.warning("GitHub API returned status %s: %s", resp.status_code, resp.text[:200])
    except Exception as e:
        log.warning("Could not query GitHub commit API: %s", e)

    short_sha = commit_sha[:7] if commit_sha else "latest"

    local_sha = ""
    hash_file = ".commit_hash"
    if os.path.exists(hash_file):
        try:
            with open(hash_file, "r", encoding="utf-8") as f:
                local_sha = f.read().strip()
        except Exception:
            local_sha = ""

    if only_check:
        if local_sha and commit_sha and local_sha == commit_sha:
            await reply_or_edit(
                event,
                f"✅ <b>Your userbot is already up to date!</b>\n"
                f"• <b>Source:</b> <code>GitHub ({owner}/{repo_name}@{branch})</code>\n"
                f"• <b>Commit:</b> <code>{short_sha}</code>\n"
                f"• <i>No new updates found upstream.</i>",
                parse_mode="html"
            )
            return

        await reply_or_edit(
            event,
            f"📦 <b>Update available from GitHub!</b>\n\n"
            f"• <b>Repo:</b> <code>{owner}/{repo_name}</code>\n"
            f"• <b>Branch:</b> <code>{branch}</code>\n"
            f"• <b>Latest Commit:</b> <code>{short_sha}</code>\n"
            f"• <b>Author:</b> <i>{html.escape(commit_author)}</i>\n"
            f"• <b>Changelog:</b> <code>{html.escape(commit_msg)}</code>\n\n"
            f"💡 <i>Run <code>.update</code> to download and install now.</i>",
            parse_mode="html"
        )
        return

    if not force and local_sha and commit_sha and local_sha == commit_sha:
        await reply_or_edit(
            event,
            f"✅ <b>Your userbot is already up to date!</b>\n"
            f"• <b>Source:</b> <code>GitHub ({owner}/{repo_name}@{branch})</code>\n"
            f"• <b>Commit:</b> <code>{short_sha}</code>\n"
            f"• <i>No new updates found upstream.</i>\n\n"
            f"💡 <i>Use <code>.update -f</code> to re-download and force refresh all files.</i>",
            parse_mode="html"
        )
        return

    await reply_or_edit(
        event,
        f"📦 <b>Downloading updates from GitHub...</b>\n\n"
        f"• <b>Repository:</b> <code>{owner}/{repo_name}</code>\n"
        f"• <b>Branch:</b> <code>{branch}</code>\n"
        f"• <b>Commit:</b> <code>{short_sha}</code>\n"
        f"⚡ <i>Fetching zip archive (preserving .env and session files)...</i>",
        parse_mode="html"
    )

    zip_urls = [
        f"https://codeload.github.com/{owner}/{repo_name}/zip/refs/heads/{branch}",
        f"https://github.com/{owner}/{repo_name}/archive/refs/heads/{branch}.zip"
    ]

    zip_bytes = None
    last_dl_err = ""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client_http:
        for z_url in zip_urls:
            try:
                resp = await client_http.get(z_url, headers=headers)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    zip_bytes = resp.content
                    break
                else:
                    last_dl_err = f"HTTP {resp.status_code}"
            except Exception as e:
                last_dl_err = str(e)

    if not zip_bytes:
        await reply_or_edit(
            event,
            f"❌ <b>Download failed:</b> <code>{html.escape(last_dl_err or 'Could not download archive zip')}</code>",
            parse_mode="html"
        )
        return

    def is_protected(path_rel: str) -> bool:
        norm = os.path.normpath(path_rel)
        base = os.path.basename(norm).lower()
        if base in {
            ".env", ".env.backup", ".env.production",
            "bot_settings.json", "bot_memories.json",
            "sudo_users.json", "promoted_users.json",
            "auth_gcs.json", "userbot.log", "hwh_userbot.session"
        }:
            if os.path.exists(norm):
                return True
        if base.endswith(".session") or base.endswith(".session-journal"):
            if os.path.exists(norm):
                return True
        if base.endswith((".db", ".sqlite", ".sqlite3")):
            if os.path.exists(norm):
                return True
        parts = norm.split(os.sep)
        if parts[0] in ("logs", ".git", ".pytest_cache", ".src_vault"):
            return True
        return False

    old_req = ""
    if os.path.exists("requirements.txt"):
        try:
            with open("requirements.txt", "r", encoding="utf-8") as f:
                old_req = f.read()
        except Exception:
            pass

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    namelist = zf.namelist()
    if not namelist:
        await reply_or_edit(event, "❌ <b>Error:</b> Downloaded archive is empty.", parse_mode="html")
        return

    prefix = namelist[0].split("/")[0] + "/"
    updated_files_count = 0

    for member in zf.infolist():
        if not member.filename.startswith(prefix):
            continue
        rel = member.filename[len(prefix):]
        if not rel:
            continue
        if member.is_dir():
            os.makedirs(rel, exist_ok=True)
            continue

        if is_protected(rel):
            continue

        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        file_data = zf.read(member.filename)
        with open(rel, "wb") as f_out:
            f_out.write(file_data)
        updated_files_count += 1

    if commit_sha:
        try:
            with open(hash_file, "w", encoding="utf-8") as f:
                f.write(commit_sha)
        except Exception:
            pass

    new_req = ""
    if os.path.exists("requirements.txt"):
        try:
            with open("requirements.txt", "r", encoding="utf-8") as f:
                new_req = f.read()
        except Exception:
            pass

    if old_req and new_req and old_req != new_req:
        await reply_or_edit(
            event,
            "📦 <i>Requirements changed, updating dependencies...</i>",
            parse_mode="html"
        )
        try:
            pip_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", "requirements.txt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await pip_proc.communicate()
        except Exception as e:
            log.warning("pip install error during http update: %s", e)

    chat_id = event.chat_id
    msg_id = getattr(status_msg, "id", None) or getattr(event, "id", None)
    database.set_restart_state(
        chat_id,
        msg_id or "",
        note=f"🚀 <b>Userbot successfully updated ({short_sha}, {updated_files_count} files) and is back online!</b>"
    )

    await reply_or_edit(
        event,
        f"🚀 <b>Update complete ({updated_files_count} files updated)! Restarting userbot in 2 seconds...</b>",
        parse_mode="html"
    )

    log.info("HTTP Update complete. Restarting process...")
    await asyncio.sleep(1.5)

    try:
        await client.disconnect()
    except Exception:
        pass

    args = [sys.executable] + (sys.argv if sys.argv else ["main.py"])
    try:
        os.execv(sys.executable, args)
    except Exception as e:
        log.error("os.execv failed after http update: %s. Using sys.exit(0)...", e)
        sys.exit(0)

@client.on(events.NewMessage(pattern=r"^[,.]update(?:\s+(.*))?$"))
async def update_cmd(event):
    """
    Checks for updates from the git repository or GitHub API, pulls latest commits, and restarts the userbot.
    RESTRICTED TO BOT OWNER ONLY.
    Usage:
      .update          -> Checks and pulls updates if available, then restarts.
      .update check    -> Only checks if an update is available without pulling.
      .update force/-f -> Resets hard to upstream origin and pulls cleanly.
    """
    if not is_owner(event):
        if is_authorized(event):
            await reply_or_edit(
                event,
                "⛔ <b>Permission Denied:</b> Sirf bot owner hi update kar sakte hain.",
                parse_mode="html"
            )
        return

    arg = (event.pattern_match.group(1) or "").strip().lower()
    only_check = (arg == "check")
    force = (arg in ["force", "-f", "--force"])

    git_bin = find_git_binary()
    has_git = bool(git_bin and os.path.exists(".git"))

    # If git CLI is not installed or .git folder is missing, use Direct GitHub HTTP/Zip updater!
    if not has_git:
        log.info("Git binary or .git not available. Using Direct GitHub HTTP/Zip Updater.")
        await perform_http_zip_update(event, only_check, force)
        return

    status_msg = await reply_or_edit(
        event,
        "🔍 <b>Checking for updates from upstream repository...</b>",
        parse_mode="html"
    )

    try:
        branch_proc = await asyncio.create_subprocess_exec(
            git_bin or "git", "rev-parse", "--abbrev-ref", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        b_out, _ = await branch_proc.communicate()
        branch = b_out.decode().strip() or getattr(config, "UPSTREAM_BRANCH", "main") or "main"

        target_remote = "origin"
        target_branch = branch
        try:
            upstream_repo = getattr(config, "UPSTREAM_REPO", "")
            if upstream_repo:
                chk_up = await asyncio.create_subprocess_exec(
                    git_bin or "git", "remote", "get-url", "upstream",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await chk_up.communicate()
                if chk_up.returncode != 0:
                    add_up = await asyncio.create_subprocess_exec(
                        git_bin or "git", "remote", "add", "upstream", upstream_repo,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await add_up.communicate()
                target_remote = "upstream"
                target_branch = getattr(config, "UPSTREAM_BRANCH", branch)
        except Exception:
            target_remote = "origin"
            target_branch = branch

        fetch_proc = await asyncio.create_subprocess_exec(
            git_bin or "git", "fetch", target_remote, target_branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await fetch_proc.communicate()

        count_proc = await asyncio.create_subprocess_exec(
            git_bin or "git", "rev-list", f"HEAD...{target_remote}/{target_branch}", "--count",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        c_out, _ = await count_proc.communicate()
        try:
            count = int(c_out.decode().strip() or 0)
        except ValueError:
            count = 0

        if count == 0:
            await reply_or_edit(
                event,
                f"✅ <b>Your userbot is already up to date!</b>\n"
                f"• <b>Source:</b> <code>{target_remote}/{target_branch}</code>\n"
                f"• <i>No new commits found upstream.</i>",
                parse_mode="html"
            )
            return

        log_proc = await asyncio.create_subprocess_exec(
            git_bin or "git", "log", f"HEAD..{target_remote}/{target_branch}", "-n", "5", "--pretty=format:• %s (<code>%h</code>)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        l_out, _ = await log_proc.communicate()
        changelog = l_out.decode().strip() or "Various bugfixes and improvements"

        if only_check:
            await reply_or_edit(
                event,
                f"📦 <b>Found {count} new update(s) available!</b>\n\n"
                f"<b>Recent Changelog:</b>\n{changelog}\n\n"
                f"💡 <i>Run <code>.update</code> to install now.</i>",
                parse_mode="html"
            )
            return

        await reply_or_edit(
            event,
            f"📦 <b>Found {count} update(s)! Upgrading...</b>\n\n"
            f"<b>Changelog:</b>\n{changelog}\n\n"
            f"⚡ <i>Pulling latest code from {target_remote}/{target_branch}...</i>",
            parse_mode="html"
        )

        if force:
            reset_proc = await asyncio.create_subprocess_exec(
                git_bin or "git", "reset", "--hard", f"{target_remote}/{target_branch}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await reset_proc.communicate()

        pull_proc = await asyncio.create_subprocess_exec(
            git_bin or "git", "pull", target_remote, target_branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        p_out, p_err = await pull_proc.communicate()

        if pull_proc.returncode != 0 and not force:
            err_text = p_err.decode().strip() or p_out.decode().strip()
            await reply_or_edit(
                event,
                f"⚠️ <b>Git Pull Conflict!</b>\n"
                f"<code>{html.escape(err_text[:300])}</code>\n\n"
                f"💡 <i>Run <code>.update -f</code> to overwrite local changes and pull cleanly.</i>",
                parse_mode="html"
            )
            return

        req_changed_proc = await asyncio.create_subprocess_exec(
            git_bin or "git", "diff", "HEAD@{1}", "HEAD", "--name-only",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        rc_out, _ = await req_changed_proc.communicate()
        if "requirements.txt" in rc_out.decode():
            await reply_or_edit(
                event,
                "📦 <i>Requirements changed, updating dependencies...</i>",
                parse_mode="html"
            )
            pip_proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--no-cache-dir", "-r", "requirements.txt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await pip_proc.communicate()

        chat_id = event.chat_id
        msg_id = getattr(status_msg, "id", None) or getattr(event, "id", None)
        database.set_restart_state(
            chat_id,
            msg_id or "",
            note=f"🚀 <b>Userbot successfully updated ({count} commit{'s' if count > 1 else ''}) and is back online!</b>"
        )

        await reply_or_edit(
            event,
            "🚀 <b>Update complete! Restarting userbot in 2 seconds...</b>",
            parse_mode="html"
        )

        log.info("Update complete. Restarting process...")
        await asyncio.sleep(1.5)

        try:
            await client.disconnect()
        except Exception:
            pass

        args = [sys.executable] + (sys.argv if sys.argv else ["main.py"])
        try:
            os.execv(sys.executable, args)
        except Exception as e:
            log.error("os.execv failed after update: %s. Using sys.exit(0)...", e)
            sys.exit(0)

    except (FileNotFoundError, OSError) as e:
        log.warning("Git execution failed with error (%s). Falling back to Direct GitHub HTTP/Zip Updater.", e)
        await perform_http_zip_update(event, only_check, force)
    except Exception as e:
        log.error("Error during .update: %s", e, exc_info=True)
        await reply_or_edit(
            event,
            f"❌ <b>Update error:</b> <code>{html.escape(str(e))}</code>",
            parse_mode="html"
        )

@client.on(events.NewMessage(pattern=r"^\.(?:active|models)$"))
async def active_models_cmd(event):
    """
    Instantly displays all AI models and operational status with green ticks.
    No network latency, zero provider names.
    """
    if not is_authorized(event):
        return
    report = gpt_service.get_active_models_summary()
    await reply_or_edit(event, report, parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.act$"))
async def act_models_cmd(event):
    """
    Performs live connectivity and latency ping test across all AI models,
    displaying green ticks with live response times and zero provider names.
    """
    if not is_authorized(event):
        return
    msg = await reply_or_edit(event, "🔄 <i>Bot ke sabhi models ka live connectivity test ho raha hai... Kripya thoda wait karein.</i>", parse_mode="html")
    report = await gpt_service.check_all_models_status()
    await msg.edit(report, parse_mode="html")

_cached_bot_username: str | None = None

async def get_bot_username() -> str | None:
    global _cached_bot_username
    if _cached_bot_username:
        return _cached_bot_username
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe")
            if r.status_code == 200:
                uname = r.json().get("result", {}).get("username")
                if uname:
                    _cached_bot_username = uname
                    return uname
    except Exception as e:
        log.warning("Could not fetch bot username: %s", e)
    return None

@client.on(events.NewMessage(pattern=r"^\.(?:cmd|cmds|help)(?:\s+([a-zA-Z0-9_-]+))?$"))
async def cmd_list_cmd(event):
    """Displays category-based interactive commands menu using inline bot or direct reply."""
    if not is_authorized(event):
        return
    cat_arg = event.pattern_match.group(1) if event.pattern_match else None
    resolved_cat = help_menu.resolve_category_alias(cat_arg)

    if resolved_cat:
        cat_text = help_menu.get_category_text(resolved_cat)
        await reply_or_edit(event, cat_text, parse_mode="html")
        return

    await reply_or_edit(event, help_menu.get_fallback_menu_text(), parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.inline(?:\s+(.*))?$"))
async def inline_cmd(event):
    """Triggers inline bot query directly from the userbot into the chat."""
    if not is_authorized(event):
        return
    query = (event.pattern_match.group(1) or "help").strip()
    bot_uname = await get_bot_username()
    if not bot_uname:
        await reply_or_edit(event, "❌ Bot username nahi mila.")
        return
    try:
        results = await client.inline_query(bot_uname, query)
        if results:
            await results[0].click(
                event.chat_id,
                reply_to=getattr(event, "reply_to_msg_id", None) or getattr(event, "id", None)
            )
            if event.out:
                try: await event.delete()
                except Exception: pass
        else:
            await reply_or_edit(event, f"❌ Koi inline result nahi mila for <code>@{bot_uname} {query}</code>", parse_mode="html")
    except Exception as e:
        await reply_or_edit(event, f"⚠️ Inline query error: <code>{e}</code>", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.humanize(?:\s+(on|off|status))?$"))
async def humanize_cmd(event):
    """Toggles human-like typing delay before solving game quizzes."""
    if not is_authorized(event):
        return
    arg = (event.pattern_match.group(1) or "").lower().strip()
    if arg == "on":
        database.set_humanize_enabled(True)
        await reply_or_edit(
            event,
            "🤖 <b>Quiz Humanizer: ON</b>\n<i>A natural typing delay (1s - 8s) with typing status will be simulated before sending each answer.</i>",
            parse_mode="html"
        )
    elif arg == "off":
        database.set_humanize_enabled(False)
        await reply_or_edit(
            event,
            "⚡ <b>Quiz Humanizer: OFF</b>\n<i>Answers will be sent at maximum speed.</i>",
            parse_mode="html"
        )
    else:
        status = "ON (Delay Active)" if database.is_humanize_enabled() else "OFF (Instant Speed)"
        await reply_or_edit(
            event,
            f"ℹ️ <b>Quiz Humanizer Status:</b> <code>{status}</code>\n<i>Use <code>.humanize on</code> or <code>.humanize off</code> to toggle.</i>",
            parse_mode="html"
        )

@client.on(events.NewMessage(pattern=r"^\.gcreply(?:\s+(on|off|status))?$"))
async def gcreply_cmd(event):
    """Toggles whether userbot ID itself replies directly with answers in authorized group chats."""
    if not is_authorized(event):
        return
    arg = (event.pattern_match.group(1) or "").lower().strip()
    if arg == "on":
        database.set_userbot_gc_reply_enabled(True)
        await reply_or_edit(
            event,
            "📢 <b>Userbot GC Reply: ON</b>\n<i>Userbot ID will send 1 word per message in GC with cross-check verification.</i>",
            parse_mode="html"
        )
    elif arg == "off":
        database.set_userbot_gc_reply_enabled(False)
        await reply_or_edit(
            event,
            "🤫 <b>Userbot GC Reply: OFF</b>\n<i>Stealth Mode: Answers are only sent to your Bot DM.</i>",
            parse_mode="html"
        )
    else:
        st = "ON (1 Word/Msg GC Reply)" if database.is_userbot_gc_reply_enabled() else "OFF (Stealth / Bot DM Only)"
        await reply_or_edit(
            event,
            f"ℹ️ <b>Userbot GC Reply:</b> <code>{st}</code>\n<i>Use <code>.gcreply on</code> or <code>.gcreply off</code> to toggle.</i>",
            parse_mode="html"
        )

@client.on(events.NewMessage(pattern=r"^\.kang(?:\s+(.*))?$"))
async def kang_cmd(event):
    """
    Kangs replied sticker, photo, GIF, or video into user's sticker pack.
    - Edits message or replies 'kanging...'
    - If successful, updates to 'kanged successfully'
    - On 1st kang of a pack, sends pack link to owner via the Telegram bot.
    """
    if not is_authorized(event):
        return

    reply = await event.get_reply_message()
    target_msg = reply if reply else event.message

    format_type, default_emoji, is_video_sticker = kang.detect_media_info(target_msg)
    if not format_type:
        await reply_or_edit(event, "❌ Kisi sticker, photo, GIF ya video par reply karke <code>.kang</code> likhein.", parse_mode="html")
        return

    status_msg = await reply_or_edit(event, "kanging...")

    custom_text = event.pattern_match.group(1)
    if custom_text and custom_text.strip():
        emojis = kang.extract_emojis(custom_text.strip())
    elif default_emoji:
        emojis = kang.extract_emojis(default_emoji)
    else:
        emojis = ["🤔"]

    try:
        raw_bytes = await client.download_media(target_msg, file=bytes)
        if not raw_bytes:
            await status_msg.edit("❌ Media download nahi ho paaya.")
            return

        if format_type == "static":
            sticker_bytes = kang.prepare_static_sticker(raw_bytes)
        elif format_type == "animated":
            sticker_bytes = raw_bytes
        elif format_type == "video":
            sticker_bytes = kang.prepare_video_sticker(raw_bytes, is_already_video_sticker=is_video_sticker)
        else:
            await status_msg.edit("❌ Unsupported media format.")
            return

        me = await client.get_me()
        user_id = me.id
        user_name = me.first_name or "User"

        pack_name, pack_title, is_first_kang = await kang.add_or_create_sticker(
            user_id=user_id,
            user_display_name=user_name,
            sticker_bytes=sticker_bytes,
            sticker_format=format_type,
            emojis=emojis
        )

        if is_first_kang:
            await kang.notify_owner_new_pack(pack_name, pack_title)

        pack_url = f"https://t.me/addstickers/{pack_name}"
        await status_msg.edit(
            f"✅ <b>Kanged successfully!</b>\n"
            f"📦 <b>Pack:</b> <a href=\"{pack_url}\">{pack_title}</a>",
            parse_mode="html"
        )

    except Exception as e:
        log.exception("Kang failed: %s", e)
        await status_msg.edit(f"❌ Kang error: <code>{e}</code>", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.(?:packs|pack)$"))
async def packs_cmd(event):
    """Lists sticker pack links for owner and sudo users."""
    if not is_authorized(event):
        return
    me = await client.get_me()
    packs = await kang.get_user_packs(me.id)
    if not packs and config.OWNER_ID:
        packs = await kang.get_user_packs(config.OWNER_ID)
    if packs:
        lines = ["📦 <b>Your Sticker Packs:</b>\n"]
        for p in packs:
            lines.append(f"• <b>{p['title']}</b> ({p['type']} - {p['count']} stickers)\n  🔗 {p['link']}")
        await reply_or_edit(event, "\n".join(lines), parse_mode="html")
    else:
        await reply_or_edit(event, "Abhi tak koi sticker pack nahi bana hai. Kisi photo/sticker/GIF par <code>.kang</code> karein!", parse_mode="html")

async def safe_deliver_ai_reply(event, status_msg, response_text: str):
    """
    Bulletproof AI response delivery:
    1. Parses LLM Markdown to Telegram HTML (<b>bold</b>, <i>italic</i>, <code>code</code>).
    2. Completely eliminates ugly unrendered asterisks (**) and raw syntax.
    3. Tries editing status_msg with rich HTML.
    4. If HTML parse fails, safely edits status_msg with clean plain text (no ** noise).
    5. If editing fails (FloodWait, deleted message), falls back to new event.reply.
    6. Chunking splits on paragraph boundaries to keep formatting tags intact.
    Guarantees the bot NEVER stays stuck on 'Thinking...'.
    """
    if not response_text or not response_text.strip():
        response_text = "⚠️ Empty response from AI."

    from core.telegram_formatter import prepare_telegram_chunks
    chunks = prepare_telegram_chunks(response_text, max_chunk=3800)
    if not chunks:
        chunks = [("⚠️ Empty response from AI.", "⚠️ Empty response from AI.")]

    first_html, first_plain = chunks[0]
    delivered = False

    if status_msg is not None:
        try:
            await status_msg.edit(first_html, parse_mode="html")
            delivered = True
        except Exception as e_html:
            log.warning("status_msg HTML edit failed (%s), falling back to clean plain text.", e_html)
            try:
                await status_msg.edit(first_plain, parse_mode=None)
                delivered = True
            except Exception as e_plain:
                log.warning("status_msg plain edit failed (%s), falling back to new reply.", e_plain)

    if not delivered:
        try:
            await event.reply(first_html, parse_mode="html")
        except Exception as e_reply_html:
            log.warning("event.reply HTML failed (%s), falling back to clean plain text.", e_reply_html)
            await event.reply(first_plain, parse_mode=None)

    for next_html, next_plain in chunks[1:]:
        try:
            await event.reply(next_html, parse_mode="html")
        except Exception:
            await event.reply(next_plain, parse_mode=None)

async def guard_gate(event) -> bool:
    """Spam/abuse gate for non-owner, non-sudo users. Returns True if the command may run."""
    if event.out or is_owner(event) or is_sudo_event(event):
        return True
    raw = event.raw_text or ""
    verdict = guard.check_command(int(event.sender_id or 0), raw)
    if verdict == "ok":
        return True
    if verdict == "silent":
        return False
    if verdict == "restricted":
        msg = "🚫 <b>Restricted:</b> Spam/abuse ki wajah se 15 minute ke liye commands band hain."
    else:
        n = verdict.split(":", 1)[1]
        msg = f"⚠️ <b>Warning {n}/{guard.MAX_STRIKES}:</b> Spam ya gaali mat karo. 3 warning par 15 min restriction."
    try:
        await event.reply(msg, parse_mode="html")
    except Exception as e:
        log.debug("guard warning send failed: %s", e)
    return False

def is_sudo_event(event) -> bool:
    sender_id = event.sender_id
    return bool(sender_id) and ctx_is_sudo(sender_id, getattr(event, "chat_id", None))

async def resolve_gpt_target(event, user_text: str | None):
    """Where the answer should be delivered.
    1. Reply target (the message the command replied to).
    2. Else @username tag in group -> that user's latest message.
    3. Else the command message itself.
    Returns the target Telethon message (or the command event message).
    """
    reply = await event.get_reply_message()
    if reply:
        return reply
    if user_text and event.is_group:
        m = re.search(r"@([A-Za-z0-9_]{4,32})", user_text)
        if m:
            try:
                async for msg in client.iter_messages(event.chat_id, search=None, limit=50):
                    sender = await msg.get_sender()
                    uname = getattr(sender, "username", None) or ""
                    if uname.lower() == m.group(1).lower() and not msg.out and msg.id != event.id:
                        return msg
            except Exception as e:
                log.debug("tag target lookup failed: %s", e)
    return event.message

async def deliver_thinking_flow(event, target, response: str):
    """`.1gpt ON` flow: 'Thinking...' reply to target -> delete it -> answer as NEW reply to target."""
    thinking = None
    try:
        thinking = await client.send_message(event.chat_id, "<i>Thinking...</i>", reply_to=target.id, parse_mode="html")
    except Exception as e:
        log.warning("Thinking message send failed: %s", e)

    from core.telegram_formatter import prepare_telegram_chunks
    chunks = prepare_telegram_chunks(response or "⚠️ Empty response from AI.", max_chunk=3800)
    if not chunks:
        chunks = [("⚠️ Empty response from AI.", "⚠️ Empty response from AI.")]

    first_html, first_plain = chunks[0]
    try:
        await client.send_message(event.chat_id, first_html, reply_to=target.id, parse_mode="html")
    except Exception as e_html:
        log.warning("Thinking flow HTML send failed (%s), falling back to clean plain text.", e_html)
        try:
            await client.send_message(event.chat_id, first_plain, reply_to=target.id, parse_mode=None)
        except Exception as e:
            log.warning("Answer send failed, falling back to event reply: %s", e)
            await event.reply(first_plain, parse_mode=None)

    for next_html, next_plain in chunks[1:]:
        try:
            await client.send_message(event.chat_id, next_html, reply_to=target.id, parse_mode="html")
        except Exception:
            await client.send_message(event.chat_id, next_plain, reply_to=target.id, parse_mode=None)

    if thinking is not None:
        try:
            await thinking.delete()
        except Exception as e:
            log.warning("Thinking delete failed (%s); editing to empty.", e)
            try:
                await thinking.edit("✅", parse_mode=None)
            except Exception:
                pass

@client.on(events.NewMessage(pattern=r"^\.1gpt(?:\s+(on|off|status))?$"))
async def gpt_thinking_cmd(event):
    """Owner/sudo: toggles per-chat thinking mode for .gpt."""
    if not is_authorized(event):
        return
    action = (event.pattern_match.group(1) or "status").lower()
    if action == "on":
        database.set_gpt_thinking_mode(event.chat_id, True)
        text = "🧠 <b>.1gpt:</b> <code>ON</code> ✅\n<i>Is chat mein .gpt ka answer 'Thinking...' ke baad naye message mein aayega.</i>"
    elif action == "off":
        database.set_gpt_thinking_mode(event.chat_id, False)
        text = "⏸️ <b>.1gpt:</b> <code>OFF</code>\n<i>Purana .gpt behaviour wapas.</i>"
    else:
        st = "ON" if database.is_gpt_thinking_mode(event.chat_id) else "OFF"
        text = f"🧠 <b>.1gpt Status:</b> <code>{st}</code>"
    await reply_or_edit(event, text, parse_mode="html")

@client.on(events.NewMessage(pattern=r"^\.gpt(?:\s+([\s\S]+))?$"))
async def gpt_cmd(event):
    """
    Executes AI query (.gpt or /gpt):
    1. If replying to or attached with an image: Uses Mistral Vision (pixtral-12b-2409).
    2. If text only: Uses ultra-fast AI cascade (Healthy Apinex -> Groq LPU -> Cloudflare 70B -> Mistral -> Pollinations).
       - Direct question: .gpt <query> or /gpt <query>
       - Reply to user: .gpt or /gpt
       - Instruction on user's message: .gpt <instruction>
       - Photo with text: sends image + caption to Mistral Vision
    """
    if not is_authorized(event):
        return
    if not await guard_gate(event):
        return

    user_text = event.pattern_match.group(1)
    reply = await event.get_reply_message()
    thinking_mode = database.is_gpt_thinking_mode(event.chat_id)

    has_image = False
    image_msg = None

    if getattr(event.message, "photo", None) or (getattr(event.message, "file", None) and getattr(event.message.file, "mime_type", "").startswith("image/")):
        has_image = True
        image_msg = event.message

    elif reply:
        if getattr(reply, "photo", None) or (getattr(reply, "file", None) and getattr(reply.file, "mime_type", "").startswith("image/")) or getattr(reply, "sticker", None):
            has_image = True
            image_msg = reply

    reply_context = None
    if reply:
        reply_context = reply.raw_text or reply.message or ""

    if not user_text and not reply_context and not has_image:
        await reply_or_edit(event, "❌ Kripya question likhein ya kisi message/photo par reply karke <code>.gpt</code> karein.", parse_mode="html")
        return

    if thinking_mode:
        target = await resolve_gpt_target(event, user_text)
        if target is not None and target.id != event.id:
            reply_context = target.raw_text or target.message or reply_context
        try:
            image_bytes = None
            if has_image and image_msg:
                image_bytes = await asyncio.wait_for(client.download_media(image_msg, file=bytes), timeout=12.0)
            effective_prompt = (user_text or "").strip()
            if not effective_prompt and has_image:
                effective_prompt = "Describe this image in detail and answer any questions shown in it."
            try:
                async with client.action(event.chat_id, "typing"):
                    response = await asyncio.wait_for(
                        gpt_service.query_gpt(prompt=effective_prompt, context=reply_context, image_bytes=image_bytes),
                        timeout=18.0,
                    )
            except asyncio.TimeoutError:
                response = "⏱️ AI response lene mein time lag gaya. Thodi der baad try karein."
            await deliver_thinking_flow(event, target, response or "")
        except Exception as e:
            log.exception("GPT thinking flow failed: %s", e)
            await event.reply(f"⚠️ Error: {e}", parse_mode=None)
        return

    status_msg = await reply_or_edit(event, "<i>Thinking...</i>", parse_mode="html")

    try:
        image_bytes = None
        if has_image and image_msg:
            try:
                image_bytes = await asyncio.wait_for(client.download_media(image_msg, file=bytes), timeout=12.0)
            except Exception as e_down:
                log.warning("Media download failed or timed out: %s", e_down)
                await safe_deliver_ai_reply(event, status_msg, "⚠️ Photo download nahi ho paayi. Kripya dobara try karein.")
                return

        effective_prompt = (user_text or "").strip()
        if not effective_prompt and has_image:
            effective_prompt = "Describe this image in detail and answer any questions shown in it."

        try:
            async with client.action(event.chat_id, "typing"):
                response = await asyncio.wait_for(
                    gpt_service.query_gpt(prompt=effective_prompt, context=reply_context, image_bytes=image_bytes),
                    timeout=18.0
                )
        except asyncio.TimeoutError:
            log.warning("GPT query timed out after 18s")
            response = "⏱️ <i>AI response lene mein time lag gaya. Kripya thodi der baad dobara try karein.</i>"

        await safe_deliver_ai_reply(event, status_msg, response)

    except Exception as e:
        log.exception("GPT query failed: %s", e)
        await safe_deliver_ai_reply(event, status_msg, f"⚠️ Error: <code>{e}</code>")

def parse_telegram_message_link(text: str) -> dict[str, Any] | None:
    """
    Parses a Telegram message link for private or public channels/groups.
    Supported formats:
    - https://t.me/c/<channel_id>/<message_id>
    - https://t.me/c/<channel_id>/<topic_id>/<message_id>
    - https://t.me/<username>/<message_id>
    - https://t.me/<username>/<topic_id>/<message_id>
    - https://t.me/s/<username>/<message_id>
    - t.me/..., telegram.me/..., telegram.dog/...
    - web.telegram.org/k/#-100... or /a/#...
    - tg://privatepost?channel=...&post=...
    """
    if not text or not isinstance(text, str):
        return None

    m = re.search(r'tg:\/\/privatepost\?channel=(\d+)&post=(\d+)', text, re.IGNORECASE)
    if m:
        return {'type': 'private', 'channel_id': int(m.group(1)), 'message_id': int(m.group(2))}

    m = re.search(r'web\.telegram\.org\/[ak]\/#(?:-100|-)?(\d+)_(\d+)', text, re.IGNORECASE)
    if m:
        return {'type': 'private', 'channel_id': int(m.group(1)), 'message_id': int(m.group(2))}

    m = re.search(
        r'(?:https?:\/\/)?(?:www\.)?(?:t(?:elegram)?\.(?:me|dog))\/(?:c\/)(\d+)(?:\/\d+)?\/(\d+)',
        text,
        re.IGNORECASE
    )
    if m:
        return {'type': 'private', 'channel_id': int(m.group(1)), 'message_id': int(m.group(2))}

    m = re.search(
        r'(?:https?:\/\/)?(?:www\.)?(?:t(?:elegram)?\.(?:me|dog))\/s\/([a-zA-Z0-9_]{3,})(?:\/\d+)?\/(\d+)',
        text,
        re.IGNORECASE
    )
    if m:
        return {'type': 'public', 'username': m.group(1), 'message_id': int(m.group(2))}

    m = re.search(
        r'(?:https?:\/\/)?(?:www\.)?(?:t(?:elegram)?\.(?:me|dog))\/([a-zA-Z0-9_]{3,})(?:\/\d+)?\/(\d+)',
        text,
        re.IGNORECASE
    )
    if m:
        uname = m.group(1)
        reserved = {'c', 's', 'joinchat', 'share', 'addstickers', 'addtheme', 'addemoji', 'invoice', 'login', 'socks', 'proxy'}
        if uname.lower() not in reserved:
            return {'type': 'public', 'username': uname, 'message_id': int(m.group(2))}

    return None

async def fetch_message_from_link(link: str) -> tuple[Any | None, str | None]:
    """
    Fetches a Telegram message by link from either a private channel/group
    or a public channel. Handles entity resolution and dialog iteration.
    """
    parsed = parse_telegram_message_link(link)
    if not parsed:
        return None, "❌ Invalid Telegram message link. Example: <code>.g https://t.me/c/1234567890/45</code>"

    msg_id = parsed['message_id']

    if parsed['type'] == 'private':
        raw_channel_id = parsed['channel_id']
        marked_id = int(f"-100{raw_channel_id}")
        entity = None

        try:
            entity = await client.get_entity(marked_id)
        except Exception:
            pass

        if not entity:
            try:
                from telethon.tl.types import PeerChannel
                entity = await client.get_entity(PeerChannel(raw_channel_id))
            except Exception:
                pass

        if not entity:
            try:
                async for dialog in client.iter_dialogs(limit=250):
                    if dialog.id == marked_id or getattr(dialog.entity, 'id', None) == raw_channel_id:
                        entity = dialog.entity
                        break
            except Exception as e:
                log.warning("iter_dialogs error during private channel resolution: %s", e)

        if not entity:
            try:
                entity = await client.get_entity(marked_id)
            except Exception:
                pass

        if not entity:
            return None, "❌ Us private channel ka access nahi mila. Make sure aapka account us channel me joined ho."

        try:
            target = await client.get_messages(entity, ids=msg_id)
        except Exception as e:
            log.warning("get_messages error on private channel %s: %s", marked_id, e)
            return None, f"❌ Message fetch error: {e}"

        if not target:
            return None, "❌ Message nahi mila ya delete ho chuka hai."

        return target, None

    elif parsed['type'] == 'public':
        username = parsed['username']
        try:
            entity = await client.get_entity(username)
        except Exception as e:
            log.warning("get_entity error on public channel %s: %s", username, e)
            return None, f"❌ Public channel '@{username}' access nahi mila: {e}"

        try:
            target = await client.get_messages(entity, ids=msg_id)
        except Exception as e:
            log.warning("get_messages error on public channel %s: %s", username, e)
            return None, f"❌ Message fetch error: {e}"

        if not target:
            return None, "❌ Message nahi mila ya delete ho chuka hai."

        return target, None

    return None, "❌ Unknown link type."

MAX_GRAB_FILE_SIZE = 150 * 1024 * 1024

@client.on(events.NewMessage(pattern=r"(?i)^\.(?:g|grab)(?:\s+(.*))?$"))
async def grab_to_bot(event):
    """
    Grabs the replied-to message, latest message in GC, or a linked message
    (from private or public channels, even with restricted saving/content protection),
    deletes .g from the GC if in group, and sends the raw message/media directly into the bot.
    Strictly delivers only to the invoking user's bot DM (jo user kiya uske hi bot me).
    Never provides puzzle/game solutions.
    """
    if not is_authorized(event):
        return

    chat_id = event.chat_id
    now = time.monotonic()
    if chat_id in busy or (chat_id in _grab_debounce and now - _grab_debounce[chat_id] < 2.5):
        if event.is_group:
            try: await event.delete()
            except Exception: pass
        return

    _grab_debounce[chat_id] = now
    busy.add(chat_id)

    try:
        if event.is_group:
            try: await event.delete()
            except Exception: pass

        invoker_id = get_invoker_id(event)

        arg = ""
        if hasattr(event, "pattern_match") and event.pattern_match:
            try:
                val = event.pattern_match.group(1)
                if isinstance(val, str):
                    arg = val.strip()
            except Exception:
                arg = ""
        if not arg and hasattr(event, "raw_text") and isinstance(event.raw_text, str):
            m = re.match(r"^\.(?:g|grab)(?:\s+(.*))?$", event.raw_text, re.IGNORECASE)
            if m and isinstance(m.group(1), str):
                arg = m.group(1).strip()

        is_link_grab = False
        target = None

        if arg:
            if arg.lower() in ("help", "-h", "--help", "?"):
                await notifier.text(
                    "📥 <b>Universal Content Grabber (.g / .grab)</b>\n\n"
                    "• <b>Private Group / Channel link:</b>\n"
                    "  <code>.g https://t.me/c/1234567890/45</code>\n"
                    "• <b>Public Channel link:</b>\n"
                    "  <code>.g https://t.me/channel_name/45</code>\n"
                    "• <b>Reply in Chat:</b>\n"
                    "  Reply <code>.g</code> to any message or media in chat.\n\n"
                    "<i>✅ Downloads photos, videos, audio, voice notes, documents & text directly to your bot DM, bypassing Telegram forwarding/saving restrictions!</i>",
                    target_user_id=invoker_id
                )
                return
            parsed = parse_telegram_message_link(arg)
            if parsed:
                is_link_grab = True
                target, err = await fetch_message_from_link(arg)
                if err:
                    await notifier.text(err, target_user_id=invoker_id)
                    return
            else:
                reply_target = await event.get_reply_message() if hasattr(event, "get_reply_message") else None
                if not reply_target:
                    await notifier.text("❌ Invalid Telegram message link.\nExample: <code>.g https://t.me/c/1234567890/45</code>\nOr type <code>.g help</code> for full usage.", target_user_id=invoker_id)
                    return
                target = reply_target
        else:
            if hasattr(event, "get_reply_message"):
                target = await event.get_reply_message()

            if target and not getattr(target, 'media', None) and isinstance(getattr(target, 'raw_text', None), str):
                replied_parsed = parse_telegram_message_link(target.raw_text)
                if replied_parsed:
                    fetched_target, err = await fetch_message_from_link(target.raw_text)
                    if fetched_target:
                        target = fetched_target
                        is_link_grab = True

            if not target:
                async for m in client.iter_messages(event.chat_id, limit=3):
                    if m.id != event.id:
                        target = m
                        break

        if not target:
            await notifier.text("❌ Koi message nahi mila grab karne ke liye.", target_user_id=invoker_id)
            return

        if is_link_grab:
            target_gc = None
        else:
            target_gc = event.chat_id if event.is_group and database.is_auth_gc_for_user(event.chat_id, invoker_id) else None

        sender_name = target.post_author if isinstance(getattr(target, 'post_author', None), str) else ""
        if not sender_name:
            try:
                sender = await target.get_sender()
                if sender:
                    title = getattr(sender, 'title', '')
                    fn = getattr(sender, 'first_name', '')
                    ln = getattr(sender, 'last_name', '')
                    un = getattr(sender, 'username', '')
                    if isinstance(title, str) and title.strip():
                        sender_name = title
                    elif isinstance(fn, str) and fn.strip():
                        sender_name = fn
                        if isinstance(ln, str) and ln.strip():
                            sender_name += f" {ln}"
                        if isinstance(un, str) and un.strip():
                            sender_name += f" (@{un})"
                    elif isinstance(un, str) and un.strip():
                        sender_name = f"@{un}"
            except Exception:
                pass

        chat_title = "GC" if getattr(event, 'is_group', False) else "Chat"
        try:
            if hasattr(target, 'get_chat'):
                chat = await target.get_chat()
                if chat:
                    t = getattr(chat, 'title', None) or getattr(chat, 'first_name', None)
                    if isinstance(t, str) and t.strip():
                        chat_title = t
                else:
                    chat = await event.get_chat()
                    t = getattr(chat, 'title', None) or getattr(chat, 'first_name', None)
                    if isinstance(t, str) and t.strip():
                        chat_title = t
            else:
                chat = await event.get_chat()
                t = getattr(chat, 'title', None) or getattr(chat, 'first_name', None)
                if isinstance(t, str) and t.strip():
                    chat_title = t
        except Exception:
            pass

        if not sender_name.strip():
            sender_name = chat_title

        header = f"📥 <b>{html.escape(chat_title)}</b> | <b>{html.escape(sender_name)}</b>\n\n"
        content = target.raw_text if isinstance(getattr(target, 'raw_text', None), str) else ""

        raw_size = getattr(getattr(target, 'file', None), 'size', 0)
        file_size = raw_size if isinstance(raw_size, (int, float)) else 0
        if file_size > MAX_GRAB_FILE_SIZE:
            size_mb = round(file_size / (1024 * 1024), 1)
            limit_mb = round(MAX_GRAB_FILE_SIZE / (1024 * 1024))
            await notifier.text(
                f"{header}⚠️ <b>File size is too large ({size_mb} MB)</b>\n"
                f"Maximum allowed file size for grab is {limit_mb} MB.\n\n"
                + (f"<b>Caption:</b>\n{html.escape(content)}" if content else ""),
                extra_chat_id=target_gc,
                target_user_id=invoker_id,
            )
            return

        is_sticker = bool(getattr(target, 'sticker', None))
        if not is_sticker and getattr(target, 'document', None):
            mime_check = getattr(target.document, 'mime_type', '') or ''
            if isinstance(mime_check, str) and mime_check in ('image/webp', 'application/x-tgsticker', 'video/webm'):
                for attr in getattr(target.document, 'attributes', []):
                    if getattr(attr, '__class__', None).__name__ == 'DocumentAttributeSticker':
                        is_sticker = True
                        break

        if is_sticker:
            try:
                raw = await client.download_media(target, file=bytes)
                if raw:
                    fname = getattr(target.file, 'name', None) or "sticker.webp"
                    mime = getattr(target.file, 'mime_type', '') or "image/webp"
                    if raw.startswith(b"\x1f\x8b") or mime == "application/x-tgsticker":
                        fname = "sticker.tgs"
                        mime = "application/x-tgsticker"
                    elif raw.startswith(b"\x1a\x45\xdf\xa3") or mime == "video/webm":
                        fname = "sticker.webm"
                        mime = "video/webm"
                    await notifier.sticker(raw, filename=fname, mime_type=mime, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if content:
                        await notifier.text(header + content, extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed sticker: %s", e)

        is_gif = bool(getattr(target, 'gif', False))
        if not is_gif and getattr(target, 'document', None):
            doc_mime = getattr(target.document, 'mime_type', '') or ''
            if isinstance(doc_mime, str):
                if doc_mime == 'image/gif':
                    is_gif = True
                elif doc_mime.startswith('video/'):
                    for attr in getattr(target.document, 'attributes', []):
                        if getattr(attr, '__class__', None).__name__ == 'DocumentAttributeAnimated':
                            is_gif = True
                            break

        if is_gif:
            try:
                raw = await client.download_media(target, file=bytes)
                if raw:
                    fname = getattr(target.file, 'name', None)
                    mime = getattr(target.file, 'mime_type', '') or ''
                    if not fname:
                        fname = "animation.gif" if (raw.startswith(b"GIF8") or mime == "image/gif") else "animation.mp4"
                    cap = (header + content)[:1024]
                    await notifier.animation(raw, filename=fname, caption=cap, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if len(header + content) > 1024:
                        await notifier.text(content, extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed GIF: %s", e)

        is_voice = bool(getattr(target, 'voice', None))
        if not is_voice and getattr(target, 'document', None):
            for attr in getattr(target.document, 'attributes', []):
                if getattr(attr, '__class__', None).__name__ == 'DocumentAttributeAudio' and getattr(attr, 'voice', False):
                    is_voice = True
                    break

        if is_voice:
            try:
                raw = await client.download_media(target, file=bytes)
                if raw:
                    cap = (header + content)[:1024]
                    await notifier.voice(raw, caption=cap, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if len(header + content) > 1024:
                        await notifier.text(content, extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed voice: %s", e)

        if getattr(target, 'photo', None):
            try:
                raw = await client.download_media(target, file=bytes)
                if raw:
                    cap = (header + content)[:1024]
                    await notifier.photo(raw, cap, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if len(header + content) > 1024:
                        await notifier.text(content, extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed photo: %s", e)

        is_video_note = bool(getattr(target, 'video_note', None))
        if not is_video_note and getattr(target, 'document', None):
            for attr in getattr(target.document, 'attributes', []):
                if getattr(attr, '__class__', None).__name__ == 'DocumentAttributeVideo' and getattr(attr, 'round_message', False):
                    is_video_note = True
                    break

        if is_video_note:
            try:
                raw = await client.download_media(target, file=bytes)
                if raw and len(raw) <= MAX_GRAB_FILE_SIZE:
                    cap = (header + content)[:1024]
                    sent = False
                    if hasattr(notifier, 'video_note'):
                        sent = await notifier.video_note(raw, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if not sent:
                        sent = await notifier.video(raw, filename="video_note.mp4", caption=cap, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if not sent:
                        try:
                            await client.send_file(invoker_id, file=raw, video_note=True, caption=cap)
                        except Exception as e_direct:
                            log.warning("Direct send_file video note fallback failed: %s", e_direct)
                    if header or content:
                        await notifier.text(header + (content or "📹 Round Video Note"), extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed video note: %s", e)

        is_video = bool(getattr(target, 'video', None))
        if not is_video and getattr(target, 'document', None):
            doc_mime = getattr(target.document, 'mime_type', '') or ''
            if isinstance(doc_mime, str) and doc_mime.startswith('video/'):
                is_video = True
            else:
                for attr in getattr(target.document, 'attributes', []):
                    if getattr(attr, '__class__', None).__name__ == 'DocumentAttributeVideo' and not getattr(attr, 'round_message', False):
                        is_video = True
                        break

        if is_video:
            try:
                raw = await client.download_media(target, file=bytes)
                if raw and len(raw) <= MAX_GRAB_FILE_SIZE:
                    fname = getattr(target.file, 'name', None) or "video.mp4"
                    cap = (header + content)[:1024]
                    sent = await notifier.video(raw, filename=fname, caption=cap, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if not sent:
                        try:
                            await client.send_file(invoker_id, file=raw, caption=cap)
                        except Exception as e_direct:
                            log.warning("Direct send_file video fallback failed: %s", e_direct)
                    if len(header + content) > 1024:
                        await notifier.text(content, extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed video: %s", e)

        is_audio = bool(getattr(target, 'audio', None))
        if not is_audio and getattr(target, 'document', None):
            doc_mime = getattr(target.document, 'mime_type', '') or ''
            if isinstance(doc_mime, str) and doc_mime.startswith('audio/'):
                is_audio = True
            else:
                for attr in getattr(target.document, 'attributes', []):
                    if getattr(attr, '__class__', None).__name__ == 'DocumentAttributeAudio' and not getattr(attr, 'voice', False):
                        is_audio = True
                        break

        if is_audio:
            try:
                raw = await client.download_media(target, file=bytes)
                if raw and len(raw) <= MAX_GRAB_FILE_SIZE:
                    fname = getattr(target.file, 'name', None) or "audio.mp3"
                    cap = (header + content)[:1024]
                    sent = await notifier.audio(raw, filename=fname, caption=cap, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if not sent:
                        try:
                            await client.send_file(invoker_id, file=raw, caption=cap)
                        except Exception as e_direct:
                            log.warning("Direct send_file audio fallback failed: %s", e_direct)
                    if len(header + content) > 1024:
                        await notifier.text(content, extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed audio: %s", e)

        if getattr(target, 'poll', None):
            try:
                poll_media = target.poll
                poll_obj = getattr(poll_media, 'poll', poll_media)
                q_raw = getattr(poll_obj, 'question', 'Poll')
                question = getattr(q_raw, 'text', str(q_raw)) if not isinstance(q_raw, str) else q_raw
                is_quiz = bool(getattr(poll_obj, 'quiz', False))
                tag = "🧠 <b>Quiz:</b>" if is_quiz else "📊 <b>Poll:</b>"
                lines = [f"{header}{tag} {html.escape(str(question))}\n"]
                for i, opt in enumerate(getattr(poll_obj, 'answers', []) or [], 1):
                    opt_raw = getattr(opt, 'text', f"Option {i}")
                    opt_text = getattr(opt_raw, 'text', str(opt_raw)) if not isinstance(opt_raw, str) else opt_raw
                    lines.append(f"{i}. {html.escape(str(opt_text))}")
                await notifier.text("\n".join(lines), extra_chat_id=target_gc, target_user_id=invoker_id)
                return
            except Exception as e:
                log.warning("Could not send grabbed poll: %s", e)

        if getattr(target, 'contact', None):
            try:
                c = target.contact
                c_name = f"{getattr(c, 'first_name', '') or ''} {getattr(c, 'last_name', '') or ''}".strip() or "Contact"
                phone = getattr(c, 'phone_number', '') or "N/A"
                contact_text = f"{header}👤 <b>Contact:</b> {html.escape(c_name)}\n📞 <b>Phone:</b> <code>{html.escape(phone)}</code>"
                await notifier.text(contact_text, extra_chat_id=target_gc, target_user_id=invoker_id)
                return
            except Exception as e:
                log.warning("Could not send grabbed contact: %s", e)

        if getattr(target, 'venue', None):
            try:
                v = target.venue
                v_title = getattr(v, 'title', 'Venue') or 'Venue'
                v_addr = getattr(v, 'address', '') or ''
                geo = getattr(v, 'geo', None)
                lat = getattr(geo, 'lat', 0) if geo else 0
                long = getattr(geo, 'long', 0) if geo else 0
                loc_text = f"{header}📍 <b>{html.escape(v_title)}</b>\n🏢 {html.escape(v_addr)}\n🌐 https://maps.google.com/?q={lat},{long}"
                await notifier.text(loc_text, extra_chat_id=target_gc, target_user_id=invoker_id)
                return
            except Exception as e:
                log.warning("Could not send grabbed venue: %s", e)
        elif getattr(target, 'geo', None):
            try:
                g = target.geo
                loc_text = f"{header}📍 <b>Location:</b>\n🌐 https://maps.google.com/?q={g.lat},{g.long}"
                await notifier.text(loc_text, extra_chat_id=target_gc, target_user_id=invoker_id)
                return
            except Exception as e:
                log.warning("Could not send grabbed location: %s", e)

        if getattr(target, 'dice', None):
            try:
                d = target.dice
                dice_text = f"{header}🎲 <b>Animated Dice:</b> {getattr(d, 'emoticon', '🎲')} = <b>{getattr(d, 'value', 0)}</b>"
                await notifier.text(dice_text, extra_chat_id=target_gc, target_user_id=invoker_id)
                return
            except Exception as e:
                log.warning("Could not send grabbed dice: %s", e)

        if getattr(target, 'media', None):
            try:
                raw = await client.download_media(target, file=bytes)
                if raw and len(raw) <= MAX_GRAB_FILE_SIZE:
                    fname = getattr(target.file, 'name', None)
                    if not fname or not isinstance(fname, str):
                        ext = getattr(target.file, 'ext', '') if hasattr(target, 'file') else ''
                        ext_str = ext if isinstance(ext, str) else ''
                        fname = f"document{ext_str}" if ext_str else "document"
                    cap = (header + content)[:1024]
                    sent = await notifier.document(raw, filename=fname, caption=cap, extra_chat_id=target_gc, target_user_id=invoker_id)
                    if not sent:
                        try:
                            await client.send_file(invoker_id, file=raw, caption=cap)
                        except Exception as e_direct:
                            log.warning("Direct send_file document fallback failed: %s", e_direct)
                    if len(header + content) > 1024:
                        await notifier.text(content, extra_chat_id=target_gc, target_user_id=invoker_id)
                    return
            except Exception as e:
                log.warning("Could not send grabbed media: %s", e)

        full_text = header + (content if content else "[No text]")
        if len(full_text) <= 4000:
            await notifier.text(full_text, extra_chat_id=target_gc, target_user_id=invoker_id)
        else:
            for i in range(0, len(full_text), 4000):
                await notifier.text(full_text[i:i+4000], extra_chat_id=target_gc, target_user_id=invoker_id)

    except Exception as e:
        log.exception("grab_to_bot error: %s", e)
    finally:
        busy.discard(chat_id)

_user_debounce: dict[int, float] = {}

async def ask_ai(user_text: str, reply_context: str | None = None, image_bytes: bytes | None = None) -> str:
    """Unified AI query using gpt_service with Mistral Vision for images and token-saving key rotation for text."""
    return await gpt_service.query_gpt(prompt=user_text, context=reply_context, image_bytes=image_bytes)

async def send_bot_reply(chat_id: int, text: str, reply_to_id: int | None = None, inline_keyboard: list | None = None):
    if not isinstance(chat_id, int):
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            log.warning("Invalid chat_id passed to send_bot_reply: %r", chat_id)
            return None
    if not isinstance(text, str) or not text.strip():
        log.warning("Empty or non-string text passed to send_bot_reply")
        return None

    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_to_message_id": reply_to_id
    }
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

    async def _send():
        async with httpx.AsyncClient(timeout=30) as c:
            res = await c.post(url, json=payload)
            if res.status_code in (429, 500, 502, 503, 504):
                res.raise_for_status()
            if res.status_code != 200:

                fallback = {k: v for k, v in payload.items() if k != "parse_mode"}
                res2 = await c.post(url, json=fallback)
                if res2.status_code in (429, 500, 502, 503, 504):
                    res2.raise_for_status()
                return res2
            return res

    try:
        from core.retry import with_backoff
        return await with_backoff(_send, max_retries=3, base_delay=0.5, max_delay=5.0)
    except Exception as e:
        log.warning("send_bot_reply failed after retries (chat_id=%s): %s", chat_id, e)

async def edit_bot_message(chat_id: int, message_id: int, text: str, inline_keyboard: list | None = None):
    """Edits an existing Telegram Bot message in-place with optional inline keyboard."""
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if inline_keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

    async def _edit():
        async with httpx.AsyncClient(timeout=20) as c:
            res = await c.post(url, json=payload)
            if res.status_code in (429, 500, 502, 503, 504):
                res.raise_for_status()
            if res.status_code != 200:

                fallback = {k: v for k, v in payload.items() if k != "parse_mode"}
                res2 = await c.post(url, json=fallback)
                if res2.status_code in (429, 500, 502, 503, 504):
                    res2.raise_for_status()
                return res2
            return res

    try:
        from core.retry import with_backoff
        return await with_backoff(_edit, max_retries=3, base_delay=0.5, max_delay=3.0)
    except Exception as e:
        log.warning("edit_bot_message failed (chat_id=%s, msg_id=%s): %s", chat_id, message_id, e)
        return None

async def delete_bot_message(chat_id: int, message_id: int) -> bool:
    """Deletes a message sent by the bot."""
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/deleteMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            res = await c.post(url, json={"chat_id": chat_id, "message_id": message_id})
            return res.status_code == 200
    except Exception as e:
        log.warning("delete_bot_message failed: %s", e)
        return False

async def answer_callback_query(callback_query_id: str, text: str | None = None, show_alert: bool = False):
    """Acknowledges an incoming callback query with optional notification toast."""
    if not callback_query_id:
        return
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = show_alert
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json=payload)
    except Exception:
        pass

async def send_typing(chat_id: int, action: str = "typing"):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendChatAction", json={
                "chat_id": chat_id,
                "action": action
            })
    except Exception:
        pass

async def download_telegram_bot_file(file_id: str) -> bytes | None:
    """Downloads a file from Telegram Bot API given its file_id (capped at 10MB)."""
    if not file_id or not isinstance(file_id, str):
        return None
    try:
        async def _download():
            url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getFile"
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.post(url, json={"file_id": file_id})
                if r.status_code == 200:
                    res_data = r.json().get("result", {})
                    fpath = res_data.get("file_path")
                    fsize = res_data.get("file_size", 0)
                    if fsize and fsize > 10 * 1024 * 1024:
                        log.warning("Telegram file %s exceeds 10MB limit (%s bytes)", file_id, fsize)
                        return None
                    if fpath:
                        dl_url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{fpath}"
                        dl = await c.get(dl_url)
                        if dl.status_code == 200:
                            if len(dl.content) > 10 * 1024 * 1024:
                                log.warning("Downloaded file %s exceeds 10MB limit", file_id)
                                return None
                            return dl.content
            return None

        return await asyncio.wait_for(_download(), timeout=30.0)
    except Exception as e:
        log.warning("Could not download file %s from Telegram Bot: %s", file_id, e)
    return None

def is_valid_image_bytes(data: bytes | None) -> bool:
    """Validate image magic bytes before passing to OCR/vision."""
    if not data or not isinstance(data, (bytes, bytearray)) or len(data) < 4:
        return False
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False

async def setup_webhook():
    app_url = os.environ.get("APP_URL", "https://hwhbot.antideploy.app").rstrip("/")
    webhook_url = f"{app_url}/webhook"

    async def _do_setup():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://api.telegram.org/bot{config.BOT_TOKEN}/setWebhook", json={
                "url": webhook_url,
                "drop_pending_updates": False,
                "secret_token": config.WEBHOOK_SIGNATURE_TOKEN
            })
            if r.status_code in (429, 500, 502, 503, 504):
                r.raise_for_status()
            return r

    try:
        from core.retry import with_backoff
        r = await with_backoff(_do_setup, max_retries=3, base_delay=1.0, max_delay=5.0)
        res_data = r.json()
        if not res_data.get("ok"):
            log.warning("Telegram setWebhook returned error: %s", res_data.get("description", "unknown"))
        else:
            log.info("Set Telegram webhook to %s", webhook_url)
    except Exception as e:
        log.warning("Failed to set webhook: %s", e)

app = FastAPI()

@app.get("/healthz")
async def healthz():
    """Bare health check endpoint for PaaS probes."""
    return {"status": "healthy"}

@app.get("/")
async def root(token: str | None = None):
    """Admin dashboard protected by WEBHOOK_SIGNATURE_TOKEN."""
    import hmac
    expected = config.WEBHOOK_SIGNATURE_TOKEN
    if not token or not expected or not hmac.compare_digest(str(token), str(expected)):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"status": "unauthorized"})

    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🐍 Viper Userbot — Hidden Word Hunt</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }
            .container { text-align: center; max-width: 600px; padding: 2rem; background-color: #1e293b; border-radius: 1rem; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1); }
            h1 { color: #38bdf8; font-size: 2.5rem; margin-bottom: 0.5rem; }
            p { font-size: 1.125rem; line-height: 1.75; color: #cbd5e1; margin-bottom: 2rem; }
            .btn { display: inline-block; padding: 0.75rem 1.5rem; background-color: #0ea5e9; color: white; text-decoration: none; border-radius: 0.5rem; font-weight: bold; transition: background-color 0.2s; }
            .btn:hover { background-color: #0284c7; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🐍 Viper Userbot</h1>
            <p>Ultra-fast Telegram Assistant, AI Suite & Hidden Word Hunt Solver. Bot service is active and running 24/7.</p>
            <a href="https://t.me/Gazarkahalwahu" class="btn" target="_blank">Developer & Support</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.post("/webhook")
async def webhook(request: Request):
    import uuid
    req_id = str(uuid.uuid4())[:8]
    tok = config.request_id_var.set(req_id)
    try:
        return await _handle_webhook(request)
    finally:
        config.request_id_var.reset(tok)

async def _handle_webhook(request: Request):
    body = await request.body()

    if len(body) > 1024 * 1024:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=413, content={"status": "payload_too_large"})

    if not core_router.verify_request(body, request.headers):
        client_ip = request.client.host if request.client else "?"
        sec_log.warning(json.dumps({
            "event": "webhook_rejected_unsigned",
            "client_ip": client_ip,
            "reason": "missing_or_invalid_signature"
        }))
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"status": "unauthorized"})

    try:
        update = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return {"status": "ok"}

    if "inline_query" in update:
        iq = update["inline_query"]
        iq_id = str(iq.get("id"))
        q_text = (iq.get("query") or "").strip().lower()

        results = [
            {
                "type": "article",
                "id": "help_menu",
                "title": "📖 Viper Userbot — Commands Hub",
                "description": "Interactive command hub with category buttons",
                "input_message_content": {
                    "message_text": help_menu.get_help_main_text(),
                    "parse_mode": "HTML"
                },
                "reply_markup": {
                    "inline_keyboard": help_menu.get_help_main_markup()
                }
            },
            {
                "type": "article",
                "id": "status_alive",
                "title": "⚡ Viper Status",
                "description": "Check if Viper Userbot & Assistant are active",
                "input_message_content": {
                    "message_text": (
                        "🐍 <b>Viper Userbot is Online & Ready!</b>\n\n"
                        "• <b>Developer:</b> @Gazarkahalwahu\n"
                        "• <b>Status:</b> 100% Operational & Fast 🚀\n"
                        "• <b>Core:</b> AI Vision Engine + Game Solvers"
                    ),
                    "parse_mode": "HTML"
                },
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "📖 Commands Menu", "callback_data": "help_main"}]
                    ]
                }
            }
        ]

        if q_text in help_menu.HELP_CATEGORIES:
            cat_text = help_menu.get_category_text(q_text)
            cat_markup = help_menu.get_category_markup(q_text)
            results.insert(0, {
                "type": "article",
                "id": f"cat_{q_text}",
                "title": f"📂 {help_menu.HELP_CATEGORIES[q_text]['title']}",
                "description": f"View {q_text} commands",
                "input_message_content": {
                    "message_text": cat_text,
                    "parse_mode": "HTML"
                },
                "reply_markup": {
                    "inline_keyboard": cat_markup
                }
            })

        async with httpx.AsyncClient(timeout=10) as c:
            try:
                await c.post(
                    f"https://api.telegram.org/bot{config.BOT_TOKEN}/answerInlineQuery",
                    json={
                        "inline_query_id": iq_id,
                        "results": results,
                        "cache_time": 1,
                        "is_personal": True
                    }
                )
            except Exception as e:
                log.warning("Failed to answer inline query: %s", e)
        return {"status": "ok"}

    ctx = core_router.ctx_from_webhook(update)
    if not ctx:
        return {"status": "ok"}

    is_callback = "callback_query" in update

    if not is_callback and core_router._debounce(ctx.chat_id, skip=(ctx.is_owner or ctx.is_sudo)):
        return {"status": "ok"}

    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb.get("id")
        cb_data = (ctx.text or cb.get("data") or "").strip()

        if cb_data == "do_auth" and ctx.sender_id:
            await answer_callback_query(cb_id, text="Authorized! ✅")
            database.add_promoted_user(ctx.sender_id)
            await send_bot_reply(ctx.chat_id, "✅ <b>Authorized!</b> Ab game answers yahan deliver honge.")
            return {"status": "ok"}

        if cb_data == "help_main":
            await answer_callback_query(cb_id)
            await edit_bot_message(
                ctx.chat_id,
                ctx.msg_id,
                help_menu.get_help_main_text(),
                inline_keyboard=help_menu.get_help_main_markup()
            )
            return {"status": "ok"}

        if cb_data.startswith("help_cat_"):
            cat_key = cb_data.replace("help_cat_", "")
            cat_text = help_menu.get_category_text(cat_key)
            cat_markup = help_menu.get_category_markup(cat_key)
            await answer_callback_query(cb_id)
            await edit_bot_message(
                ctx.chat_id,
                ctx.msg_id,
                cat_text,
                inline_keyboard=cat_markup
            )
            return {"status": "ok"}

        if cb_data == "help_close":
            await answer_callback_query(cb_id, text="Menu closed")
            deleted = await delete_bot_message(ctx.chat_id, ctx.msg_id)
            if not deleted:
                await edit_bot_message(ctx.chat_id, ctx.msg_id, "❌ <i>Help menu closed.</i>", inline_keyboard=[])
            return {"status": "ok"}

        await answer_callback_query(cb_id)
        return {"status": "ok"}

    if "message" in update:
        msg = update["message"]
        text = ctx.text
        chat_id = ctx.chat_id
        sender_id = ctx.sender_id
        msg_id = ctx.msg_id
        is_owner = ctx.is_owner
        is_sudo = ctx.is_sudo
        is_authed = ctx.is_promoted

        now = time.time()
        if not (is_owner or is_sudo):
            if chat_id in _user_debounce and now - _user_debounce[chat_id] < 1.0:
                return {"status": "ok"}
            _user_debounce[chat_id] = now
            if text.startswith(("/", ".")):
                verdict = guard.check_command(int(sender_id or 0), text)
                if verdict != "ok":
                    if verdict != "silent":
                        warn = ("🚫 Spam/abuse ki wajah se 15 min restriction." if verdict == "restricted"
                                else f"⚠️ Warning {verdict.split(':', 1)[1]}/{guard.MAX_STRIKES}: spam ya gaali mat karo.")
                        await send_bot_reply(chat_id, warn, msg_id)
                    return {"status": "ok"}

        chat_type = ctx.chat_type
        is_group_chat = chat_type in ("group", "supergroup")

        reply_to = msg.get("reply_to_message")
        reply_context = (reply_to.get("text") or reply_to.get("caption")) if reply_to else None

        if text.startswith((".gcauth", "/gcauth")):
            if not (is_owner or is_sudo):
                if is_group_chat:
                    await send_bot_reply(chat_id, "❌ Sirf Owner ya Sudo users hi GC authorize kar sakte hain.", msg_id)
                return {"status": "ok"}

            parts = text.split()
            target_gc_id = chat_id if is_group_chat else None
            gc_title = msg.get("chat", {}).get("title") or "Group Chat"

            if len(parts) > 1 and (parts[1].startswith("-") or parts[1].isdigit()):
                try:
                    target_gc_id = int(parts[1])
                    gc_title = f"GC {target_gc_id}"
                except ValueError:
                    pass

            if target_gc_id:
                invoker_id = sender_id or chat_id or config.OWNER_ID
                database.add_auth_gc(target_gc_id, gc_title, user_id=invoker_id)
                safe_title = html.escape(str(gc_title))
                await send_bot_reply(
                    chat_id,
                    f"✅ <b>Group Chat Authorized for User <code>{invoker_id}</code>!</b>\n• <b>Title:</b> {safe_title}\n• <b>Chat ID:</b> <code>{target_gc_id}</code>\n\nAb is GC mein aapke answers aur solutions deliver honge.",
                    msg_id
                )
            else:
                await send_bot_reply(chat_id, "Usage: Group ke andar <code>.gcauth</code> likhein ya <code>.gcauth &lt;chat_id&gt;</code> dein.", msg_id)
            return {"status": "ok"}

        if text.startswith((".gcunauth", "/gcunauth")):
            if not (is_owner or is_sudo):
                if is_group_chat:
                    await send_bot_reply(chat_id, "❌ Sirf Owner ya Sudo users hi GC unauthorize kar sakte hain.", msg_id)
                return {"status": "ok"}

            parts = text.split()
            target_gc_id = chat_id if is_group_chat else None
            if len(parts) > 1 and (parts[1].startswith("-") or parts[1].isdigit()):
                try:
                    target_gc_id = int(parts[1])
                except ValueError:
                    pass

            if target_gc_id:
                invoker_id = sender_id or chat_id or config.OWNER_ID
                database.remove_auth_gc(target_gc_id, user_id=invoker_id)
                await send_bot_reply(
                    chat_id,
                    f"❌ <b>Group Chat Unauthenticated for User <code>{invoker_id}</code>!</b>\n• <b>Chat ID:</b> <code>{target_gc_id}</code>\nAb is GC mein aapke koi message ya solution nahi aayenge.",
                    msg_id
                )
            else:
                await send_bot_reply(chat_id, "Usage: Group ke andar <code>.gcunauth</code> likhein ya <code>.gcunauth &lt;chat_id&gt;</code> dein.", msg_id)
            return {"status": "ok"}

        if text in (".gclist", "/gclist", ".gcauths", "/gcauths") and (is_owner or is_sudo):
            invoker_id = sender_id or chat_id or config.OWNER_ID
            gcs = database.get_auth_gcs()
            if gcs:
                lines = [f"👥 <b>Authorized Group Chats ({len(gcs)}):</b>\n"]
                for gid, title in gcs.items():
                    safe_t = html.escape(str(title)) if title else ""
                    t_str = f" - <b>{safe_t}</b>" if safe_t else ""
                    users = database.get_gc_auth_users(gid)
                    u_str = ", ".join(f"<code>{u}</code>" for u in users) if users else "None"
                    active_tag = " (✅ Active for you)" if invoker_id in users else ""
                    lines.append(f"• <code>{gid}</code>{t_str}\n  └ Users: {u_str}{active_tag}")
                await send_bot_reply(chat_id, "\n".join(lines), msg_id)
            else:
                await send_bot_reply(chat_id, "ℹ️ Koi bhi Group Chat authorized nahi hai.", msg_id)
            return {"status": "ok"}

        if is_group_chat and not database.is_auth_gc(chat_id):
            return {"status": "ok"}

        if text.startswith((".sudo", "/sudo")) and is_owner:
            parts = text.split()
            target_id = None
            if len(parts) > 1 and parts[1].isdigit():
                target_id = int(parts[1])
            elif msg.get("reply_to_message"):
                rep = msg["reply_to_message"]
                target_id = rep.get("forward_from", {}).get("id") or rep.get("from", {}).get("id")

            if target_id:
                database.add_sudo_user(target_id)
                await send_bot_reply(chat_id, f"🛡️ User <code>{target_id}</code> granted SUDO access! Ab wo aapki ID se commands chala sakta hai.", msg_id)
            else:
                await send_bot_reply(chat_id, "Usage: <code>.sudo &lt;user_id&gt;</code> ya message par reply karein.", msg_id)
            return {"status": "ok"}

        if text.startswith((".unsudo", "/unsudo")) and is_owner:
            parts = text.split()
            target_id = None
            if len(parts) > 1 and parts[1].isdigit():
                target_id = int(parts[1])
            elif msg.get("reply_to_message"):
                rep = msg["reply_to_message"]
                target_id = rep.get("forward_from", {}).get("id") or rep.get("from", {}).get("id")

            if target_id:
                database.remove_sudo_user(target_id)
                await send_bot_reply(chat_id, f"❌ User <code>{target_id}</code> removed from SUDO access.", msg_id)
            else:
                await send_bot_reply(chat_id, "Usage: <code>.unsudo &lt;user_id&gt;</code> ya message par reply karein.", msg_id)
            return {"status": "ok"}

        if text in (".sudousers", "/sudousers") and (is_owner or is_sudo):
            users = database.get_sudo_users()
            if users:
                u_list = "\n".join(f"• <code>{u}</code>" for u in users)
                await send_bot_reply(chat_id, f"🛡️ <b>Sudo Users ({len(users)}):</b>\n{u_list}", msg_id)
            else:
                await send_bot_reply(chat_id, "Koi bhi sudo user added nahi hai.", msg_id)
            return {"status": "ok"}

        if text.startswith((".auth", "/auth")):
            parts = text.split()

            if (is_owner or is_sudo) and (len(parts) > 1 or msg.get("reply_to_message")):
                target_id = None
                if len(parts) > 1 and parts[1].isdigit():
                    target_id = int(parts[1])
                elif msg.get("reply_to_message"):
                    rep = msg["reply_to_message"]
                    target_id = rep.get("forward_from", {}).get("id") or rep.get("from", {}).get("id")
                if target_id:
                    database.add_promoted_user(target_id)
                    await send_bot_reply(chat_id, f"✅ User <code>{target_id}</code> authorized successfully!", msg_id)
                    return {"status": "ok"}

            database.add_promoted_user(chat_id)
            await send_bot_reply(chat_id, "✅ <b>Authorized!</b> Ab game answers yahan deliver honge.", msg_id)
            return {"status": "ok"}

        if text.startswith((".unauth", ".unuth", "/unauth", "/unuth")):
            parts = text.split()

            if (is_owner or is_sudo) and (len(parts) > 1 or msg.get("reply_to_message")):
                target_id = None
                if len(parts) > 1 and parts[1].isdigit():
                    target_id = int(parts[1])
                elif msg.get("reply_to_message"):
                    rep = msg["reply_to_message"]
                    target_id = rep.get("forward_from", {}).get("id") or rep.get("from", {}).get("id")

                if target_id and not ctx_is_owner(target_id):
                    database.remove_promoted_user(target_id)
                    await send_bot_reply(chat_id, f"❌ User <code>{target_id}</code> unauthenticated.", msg_id)
                    try:
                        await send_bot_reply(target_id, "❌ Aapka bot access revoke kar diya gaya hai.")
                    except Exception:
                        pass
                    return {"status": "ok"}
                else:
                    await send_bot_reply(chat_id, "Usage: <code>.unauth &lt;user_id&gt;</code> ya message par reply karein.", msg_id)
                    return {"status": "ok"}
            else:

                database.remove_promoted_user(chat_id)
                await send_bot_reply(chat_id, "❌ <b>Unauthenticated.</b> Ab answers nahi milenge.", msg_id)
                return {"status": "ok"}

        if text in (".users", "/users", ".authed", "/authed") and (is_owner or is_sudo):
            users = database.get_promoted_users()
            if users:
                u_list = "\n".join(f"• <code>{u}</code>" for u in users)
                await send_bot_reply(chat_id, f"👥 <b>Authorized Users ({len(users)}):</b>\n{u_list}", msg_id)
            else:
                await send_bot_reply(chat_id, "Koi bhi extra user authorized nahi hai.", msg_id)
            return {"status": "ok"}

        if text.startswith((".packs", "/packs", ".pack", "/pack")) and (is_owner or is_sudo):
            packs = await kang.get_user_packs(config.OWNER_ID)
            if packs:
                lines = ["📦 <b>Your Sticker Packs:</b>\n"]
                for p in packs:
                    lines.append(f"• <b>{p['title']}</b> ({p['type']} - {p['count']} stickers)\n  🔗 {p['link']}")
                await send_bot_reply(chat_id, "\n".join(lines), msg_id)
            else:
                await send_bot_reply(chat_id, "Abhi tak koi sticker pack nahi bana hai. Kisi photo/sticker/GIF par <code>.kang</code> karein!", msg_id)
            return {"status": "ok"}

        if text.startswith(("/repo", ".repo")):
            repo_url = getattr(config, "UPSTREAM_REPO", "") or "https://github.com/Viperr-userbot/Viper-Userbot"
            clean_repo_url = repo_url[:-4] if repo_url.endswith(".git") else repo_url
            branch = getattr(config, "UPSTREAM_BRANCH", "main")
            repo_text = (
                "🐍 <b>Viper Userbot — Official Repository</b>\n\n"
                f"• <b>GitHub:</b> <a href=\"{clean_repo_url}\">{clean_repo_url}</a>\n"
                "• <b>Deploy 24/7:</b> <a href=\"https://app.getvoroa.com/\">Deploy on Voroa</a>\n"
                f"• <b>Branch:</b> <code>{branch}</code>\n"
                "• <b>Developer:</b> @Gazarkahalwahu\n\n"
                "⭐ <i>Repo ko Star aur Fork zaroor karein!</i>"
            )
            buttons = [
                [
                    {"text": "⭐ Star on GitHub", "url": clean_repo_url},
                    {"text": "🍴 Fork Repo", "url": f"{clean_repo_url}/fork"}
                ],
                [
                    {"text": "🚀 Deploy on Voroa", "url": "https://app.getvoroa.com/"},
                    {"text": "💬 Support", "url": "https://t.me/Gazarkahalwahu"}
                ]
            ]
            await send_bot_reply(chat_id, repo_text, msg_id, inline_keyboard=buttons)
            return {"status": "ok"}

        if text.startswith(("/ping", ".ping")):
            t0 = time.time()
            uptime = get_readable_uptime()
            ping_card = (
                "╭━━━━〔 🏓 <b>VIPER PING</b> 〕━━━━╮\n"
                "┃\n"
                "┃ ⚡ <b>Latency:</b> <code>~18.50 ms</code> [🚀 Blazing Fast]\n"
                f"┃ ⏱️ <b>Uptime:</b> <code>{uptime}</code>\n"
                "┃ 🟢 <b>State:</b> <code>Online & Active</code>\n"
                "┃ 👨‍💻 <b>Developer:</b> @Gazarkahalwahu\n"
                "┃\n"
                "╰━━━━━━━━━━━━━━━━━━━━━━━╯"
            )
            await send_bot_reply(chat_id, ping_card, msg_id)
            return {"status": "ok"}

        if text.startswith(("/start", ".start")):
            uptime = get_readable_uptime()
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            start_text = (
                "╭━━━━〔 ⚡ <b>VIPER USERBOT</b> ⚡ 〕━━━━╮\n"
                "┃\n"
                "┃ 🟢 <b>Status:</b> <code>Supercharged & Online</code>\n"
                f"┃ ⏱️ <b>Uptime:</b> <code>{uptime}</code>\n"
                "┃ 🐍 <b>Version:</b> <code>v2.5.0 Premium</code>\n"
                f"┃ ⚙️ <b>Engine:</b> <code>Python {py_ver} • Webhook Bot</code>\n"
                "┃ 🎮 <b>Solvers:</b> <code>Wordly • 10Things • Word Hunt</code>\n"
                "┃ 🤖 <b>AI Core:</b> <code>Vision & Smart Solver</code>\n"
                "┃ 👨‍💻 <b>Developer:</b> @Gazarkahalwahu\n"
                "┃\n"
                "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n\n"
                "✨ <b>Welcome to Viper Userbot!</b>\n"
                "<i>Your all-in-one assistant for automated Game Solvers, multi-model AI, and media tools.</i>"
            )
            buttons = [
                [{"text": "📋 Commands Menu", "callback_data": "help_main"}]
            ]
            if not is_authed:
                buttons.append([{"text": "🔐 .auth", "callback_data": "do_auth"}])
                extra = "\n\n⚠️ <i>Game answers receive karne ke liye neeche button dabayein ya <code>.auth</code> likhein.</i>"
            else:
                extra = "\n\n✅ <i>Aap authorized hain. Game answers yahan deliver honge.</i>"
            await send_bot_reply(chat_id, start_text + extra, msg_id, inline_keyboard=buttons)
            return {"status": "ok"}

        if text.startswith(("/cmd", ".cmd", "/cmds", ".cmds", "/help", ".help")):
            parts = text.split()
            cat_arg = parts[1] if len(parts) > 1 else None
            resolved_cat = help_menu.resolve_category_alias(cat_arg)
            if resolved_cat:
                cat_text = help_menu.get_category_text(resolved_cat)
                cat_markup = help_menu.get_category_markup(resolved_cat)
                await send_bot_reply(chat_id, cat_text, msg_id, inline_keyboard=cat_markup)
            else:
                await send_bot_reply(
                    chat_id,
                    help_menu.get_help_main_text(),
                    msg_id,
                    inline_keyboard=help_menu.get_help_main_markup()
                )
            return {"status": "ok"}

        if text.startswith(("/active", ".active", "/models", ".models")):
            report = gpt_service.get_active_models_summary()
            await send_bot_reply(chat_id, report, msg_id)
            return {"status": "ok"}

        if text in ("/act", ".act"):
            await send_typing(chat_id)
            report = await gpt_service.check_all_models_status()
            await send_bot_reply(chat_id, report, msg_id)
            return {"status": "ok"}

        if text in ("/on", ".on", "/autoreply on", ".autoreply on"):
            database.set_auto_reply_enabled(True)
            await send_bot_reply(chat_id, "🤖 <b>Bot Auto-Reply:</b> <code>ON</code> ✅\n\n<i>Ab Telegram bot aane wale sabhi messages par automatically AI reply dega.</i>", msg_id)
            return {"status": "ok"}

        if text in ("/off", ".off", "/autoreply off", ".autoreply off"):
            database.set_auto_reply_enabled(False)
            await send_bot_reply(chat_id, "🔇 <b>Bot Auto-Reply:</b> <code>OFF</code> ⏸️\n\n<i>Ab Telegram bot sirf explicit commands (.gpt, .k, .c, games) par reply karega.</i>", msg_id)
            return {"status": "ok"}

        if text.startswith(("/gcreply", ".gcreply")):
            arg = re.sub(r"^[./]gcreply\s*", "", text, flags=re.I).lower().strip()
            if arg == "on":
                database.set_userbot_gc_reply_enabled(True)
                await send_bot_reply(chat_id, "📢 <b>Userbot GC Reply: ON</b>\n<i>Userbot ID will send 1 word per message in GC with cross-check verification.</i>", msg_id)
            elif arg == "off":
                database.set_userbot_gc_reply_enabled(False)
                await send_bot_reply(chat_id, "🤫 <b>Userbot GC Reply: OFF</b>\n<i>Stealth Mode: Answers are only sent to your Bot DM.</i>", msg_id)
            else:
                st = "ON (1 Word/Msg GC Reply)" if database.is_userbot_gc_reply_enabled() else "OFF (Stealth / Bot DM Only)"
                await send_bot_reply(chat_id, f"ℹ️ <b>Userbot GC Reply:</b> <code>{st}</code>\n<i>Use <code>/gcreply on</code> or <code>/gcreply off</code></i>", msg_id)
            return {"status": "ok"}

        if text.startswith(("/humanize", ".humanize")):
            arg = re.sub(r"^[./]humanize\s*", "", text, flags=re.I).lower().strip()
            if arg == "on":
                database.set_humanize_enabled(True)
                await send_bot_reply(chat_id, "🤖 <b>Quiz Humanizer: ON</b>\n<i>A natural typing delay (1.8s - 3.2s) with typing status will be simulated before sending each answer.</i>", msg_id)
            elif arg == "off":
                database.set_humanize_enabled(False)
                await send_bot_reply(chat_id, "⚡ <b>Quiz Humanizer: OFF</b>\n<i>Answers will be sent at maximum speed.</i>", msg_id)
            else:
                st = "ON (Delay Active)" if database.is_humanize_enabled() else "OFF (Instant Speed)"
                await send_bot_reply(chat_id, f"ℹ️ <b>Quiz Humanizer Status:</b> <code>{st}</code>\n<i>Use <code>/humanize on</code> or <code>/humanize off</code></i>", msg_id)
            return {"status": "ok"}

        if text.startswith(("/yaad", ".yaad")):
            arg = re.sub(r"^[./]yaad\s*", "", text, flags=re.I).strip()
            if not arg and reply_context:
                arg = reply_context.strip()
            if not arg:
                mems = database.get_memories()
                if not mems:
                    await send_bot_reply(chat_id, "🧠 <i>Abhi tak koi memory save nahi hai. Likhein:</i> <code>/yaad &lt;baat&gt;</code>", msg_id)
                else:
                    lines = [f"🧠 <b>Bot Ki Memory ({len(mems)} Saved Facts):</b>\n"]
                    for m in mems:
                        lines.append(f"• <code>#{m['id']}</code>: <i>{html.escape(str(m.get('content', '')))}</i>")
                    lines.append("\nDelete: <code>/bhool &lt;id ya text&gt;</code>")
                    await send_bot_reply(chat_id, "\n".join(lines), msg_id)
                return {"status": "ok"}
            mem = database.add_memory(arg)
            safe_arg = html.escape(str(arg))
            await send_bot_reply(chat_id, f"🧠 <b>Yaad rakh liya!</b>\n<i>\"{safe_arg}\"</i>\n\n📌 <b>Memory ID:</b> <code>#{mem['id']}</code>\n✨ <i>Ab se .gpt mein poochne par bot ise yaad rakhega.</i>", msg_id)
            return {"status": "ok"}

        if text.startswith(("/yaadein", ".yaadein", "/memories", ".memories")):
            mems = database.get_memories()
            if not mems:
                await send_bot_reply(chat_id, "🧠 <i>Abhi tak koi memory save nahi hai. Likhein:</i> <code>/yaad &lt;text&gt;</code>", msg_id)
            else:
                lines = [f"🧠 <b>Bot Ki Memory ({len(mems)} Saved Facts):</b>\n"]
                for m in mems:
                    lines.append(f"• <code>#{m['id']}</code>: <i>{html.escape(str(m.get('content', '')))}</i>")
                lines.append("\nDelete: <code>/bhool &lt;id ya text&gt;</code>")
                await send_bot_reply(chat_id, "\n".join(lines), msg_id)
            return {"status": "ok"}

        if text.startswith(("/bhool", ".bhool", "/forget", ".forget", "/bhoolja", ".bhoolja")):
            arg = re.sub(r"^[./](?:bhool|forget|bhoolja)\s*", "", text, flags=re.I).strip()
            if not arg:
                await send_bot_reply(chat_id, "❌ <i>Kripya memory ID ya text likhein:</i> <code>/bhool &lt;id/text&gt;</code> <i>ya</i> <code>/bhool all</code>", msg_id)
                return {"status": "ok"}
            if arg.lower() in ("all", "sab", "puri"):
                count = database.clear_all_memories()
                await send_bot_reply(chat_id, f"🗑️ <b>Sabhi yaadein bhool gaya!</b> (Total {count} memories cleared) ✅", msg_id)
                return {"status": "ok"}
            ok, deleted = database.delete_memory(arg.lstrip("#"))
            if ok:
                safe_del = html.escape(str(deleted))
                await send_bot_reply(chat_id, f"🗑️ <b>Yaad bhool gaya:</b>\n<i>\"{safe_del}\"</i>\n\n✅ <i>Memory se permanently delete kar diya gaya.</i>", msg_id)
            else:
                await send_bot_reply(chat_id, "❌ <i>Matching memory nahi mili. <code>/yaadein</code> check karein.</i>", msg_id)
            return {"status": "ok"}

        if not is_authed:

            if text in (".auth", "/auth", ".start", "/start"):
                await send_bot_reply(
                    chat_id,
                    "⚠️ Pehle <code>.auth</code> karein access paane ke liye.",
                    msg_id,
                    inline_keyboard=[[{"text": "🔐 .auth", "callback_data": "do_auth"}]]
                )
            return {"status": "ok"}

        photo_list = msg.get("photo")
        doc = msg.get("document")
        photo_bytes = None

        if photo_list:

            largest = photo_list[-1]
            photo_bytes = await download_telegram_bot_file(largest.get("file_id"))
        elif doc and doc.get("mime_type", "").startswith("image/"):
            photo_bytes = await download_telegram_bot_file(doc.get("file_id"))

        if reply_to:

            if not photo_bytes and reply_to.get("photo"):
                photo_bytes = await download_telegram_bot_file(reply_to["photo"][-1].get("file_id"))

        if re.match(r"^[./](?:k|c|g|grab)(?:\s+|$)", text, re.I):
            return {"status": "ok"}

        is_gpt_cmd = bool(re.match(r"^[./]gpt(?:\s+|$)", text, re.I))
        if is_gpt_cmd:
            clean_query = re.sub(r"^[./]gpt\s*", "", text, flags=re.I).strip()
        else:
            clean_query = text

        if photo_bytes and is_valid_image_bytes(photo_bytes):

            if not is_gpt_cmd:

                if clean_query and solver.HINT_RE.search(clean_query):
                    await send_typing(chat_id, action="typing")
                    try:
                        t0 = time.time()
                        grid, disputed = await llm_vision.read_grid(photo_bytes)
                        hints = solver.parse_hints(clean_query)
                        results = solver.solve(grid, hints)
                        secs = time.time() - t0
                        report = format_report(results, disputed, secs)
                        await send_bot_reply(chat_id, report, msg_id)
                        return {"status": "ok"}
                    except Exception as e:
                        log.warning("Bot game solve failed: %s. Falling back to vision query.", e)

                if clean_query and ten_things.is_tenthings_message(clean_query):
                    await send_typing(chat_id, action="typing")
                    cat, items = ten_things.parse_message(clean_query)
                    if cat and items:
                        ans = await ten_things.solve_10things(cat, items)
                        if ans:
                            rep = f"🎯 <b>10Things Answers</b>\n<i>{cat}</i>\n\n" + "\n".join(f"{i}. <code>{a}</code>" for i, a in enumerate(ans, 1))
                            await send_bot_reply(chat_id, rep, msg_id)
                            return {"status": "ok"}

            if is_gpt_cmd or database.is_auto_reply_enabled():
                await send_typing(chat_id, action="typing")
                effective_prompt = clean_query or "Describe this image in detail and answer any questions shown in it."
                ai_reply = await ask_ai(
                    user_text=effective_prompt,
                    reply_context=reply_context,
                    image_bytes=photo_bytes
                )
                from core.telegram_formatter import markdown_to_telegram_html
                await send_bot_reply(chat_id, markdown_to_telegram_html(ai_reply or ""), msg_id)
            return {"status": "ok"}

        if text:

            if not is_gpt_cmd and ten_things.is_tenthings_message(text):
                await send_typing(chat_id, action="typing")
                cat, items = ten_things.parse_message(text)
                if cat and items:
                    ans = await ten_things.solve_10things(cat, items)
                    if ans:
                        rep = f"🎯 <b>10Things Answers</b>\n<i>{cat}</i>\n\n" + "\n".join(f"{i}. <code>{a}</code>" for i, a in enumerate(ans, 1))
                        await send_bot_reply(chat_id, rep, msg_id)
                        return {"status": "ok"}

            if is_gpt_cmd or database.is_auto_reply_enabled():
                await send_typing(chat_id, action="typing")
                query_to_send = clean_query if is_gpt_cmd else text
                ai_reply = await ask_ai(user_text=query_to_send, reply_context=reply_context)
                from core.telegram_formatter import markdown_to_telegram_html
                await send_bot_reply(chat_id, markdown_to_telegram_html(ai_reply or ""), msg_id)

        return {"status": "ok"}

async def keep_alive():
    """Pings both local web service and public URL periodically to prevent cloud sleep."""
    await asyncio.sleep(30)
    port = int(os.environ.get("PORT", 8000))
    public_url = os.environ.get("APP_URL", "https://hwhbot.antideploy.app").rstrip("/")
    local_url = f"http://127.0.0.1:{port}/healthz"
    public_url = f"{public_url}/healthz"

    from core.retry import with_backoff

    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as c:

                try:
                    await with_backoff(lambda: c.get(local_url), max_retries=2, base_delay=0.5, max_delay=2.0)
                except Exception:
                    pass

                try:
                    await with_backoff(lambda: c.get(public_url), max_retries=2, base_delay=0.5, max_delay=2.0)
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(60)

async def telethon_watchdog():
    """Periodically verifies MTProto connection health and pings to keep NAT state active."""
    await asyncio.sleep(45)
    while True:
        try:
            if client.is_connected():

                await client.get_me()
            else:
                log.warning("Watchdog detected disconnected client, attempting reconnect...")
                try:
                    await client.connect()
                except Exception as ce:
                    log.warning("Watchdog reconnect error: %s", ce)
        except Exception as e:
            log.debug("Watchdog healthcheck: %s", e)
        await asyncio.sleep(60)

def _verify_author_seal():
    """Validates the author identity watermark across core runtime."""
    try:
        from core.auth import verify_author_integrity
        verify_author_integrity()
    except Exception as e:
        log.critical("Author seal violation: %s", e)
        sys.exit("❌ Integrity Violation: Author credit (@Gazarkahalwahu) must remain intact.")

    main_py_path = os.path.abspath(__file__)
    if os.path.exists(main_py_path):
        try:
            with open(main_py_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                if "Gazarkahalwahu" not in content:
                    log.critical("CRITICAL: Rebranding / author credit (@Gazarkahalwahu) has been removed! Execution terminated.")
                    sys.exit("❌ Integrity Violation: Author credit (@Gazarkahalwahu) must remain intact.")
        except SystemExit:
            raise
        except Exception:
            pass

async def run_telethon():
    """Runs Telethon client until disconnected."""
    _verify_author_seal()
    if not client.is_connected():
        log.info("Connecting Telethon client...")
        await client.connect()

    if not await client.is_user_authorized():
        log.info("Client not authorized, starting login...")
        await client.start()

    me = await client.get_me()
    log.info("Telethon online as %s (ID: %s)", getattr(me, 'username', '') or me.first_name, me.id)

    try:
        restart_info = database.get_and_clear_restart_state()
        if restart_info:
            target_chat, target_mid = restart_info
            note = database.get_and_clear_restart_note()
            text_to_send = note or "i m back"
            log.info("Pending restart state found: sending '%s' to %s (reply to %s)", text_to_send, target_chat, target_mid)
            try:
                if note:
                    await client.send_message(target_chat, text_to_send, reply_to=target_mid, parse_mode="html")
                else:
                    await client.send_message(target_chat, text_to_send, reply_to=target_mid)
            except Exception as se:
                log.warning("Could not reply with restart text to %s: %s. Sending standalone...", target_chat, se)
                try:
                    if note:
                        await client.send_message(target_chat, text_to_send, parse_mode="html")
                    else:
                        await client.send_message(target_chat, text_to_send)
                except Exception as se2:
                    log.error("Failed sending restart text: %s", se2)
    except Exception as re_err:
        log.warning("Error checking restart state on boot: %s", re_err)

    await client.run_until_disconnected()
    log.warning("Telethon disconnected cleanly.")

_web_server = None

async def run_web_server():
    """Runs Uvicorn web server with isolated signals."""
    global _web_server
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting web server on port %s", port)
    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
    server = uvicorn.Server(server_config)
    server.install_signal_handlers = lambda: None
    _web_server = server
    await server.serve()
    log.warning("Web server stopped.")

extra_plugins.register(client, is_authorized, is_owner)

async def supervised_task(name: str, coro_fn):
    """Supervisor wrapper that auto-restarts individual services with exponential backoff and crash window protection."""
    backoff = 2.0
    crash_times = []
    while True:
        try:
            log.info("Service [%s] starting...", name)
            await coro_fn()
            log.warning("Service [%s] exited cleanly. Restarting in %ss...", name, backoff)
        except asyncio.CancelledError:
            log.info("Service [%s] cancelled.", name)
            break
        except Exception as e:
            now = time.time()
            crash_times.append(now)

            crash_times = [t for t in crash_times if now - t <= 60]
            if len(crash_times) > 10:
                log.critical("Service [%s] crash loop detected (>10 crashes in 60s): %s. Throttling...", name, e)
                backoff = 30.0
            else:
                log.error("Service [%s] crashed: %s. Restarting in %ss...", name, e, backoff, exc_info=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 30.0)

def _install_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """SIGINT/SIGTERM -> graceful shutdown event (Windows-safe)."""
    loop = asyncio.get_running_loop()

    def _trigger_shutdown(sig):
        sig_name = getattr(sig, "name", str(sig))
        sec_log.info(json.dumps({
            "event": "shutdown_signal",
            "signal": sig_name,
            "reason": "Termination signal received"
        }))
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _trigger_shutdown(s))
        except (NotImplementedError, RuntimeError, ValueError):
            pass

def _startup_banner() -> str:
    hmac_state = "ON (secret_token)" if config.WEBHOOK_SIGNATURE_TOKEN else "OFF - UNSECURE"
    return (
        "=== Hidden Word Hunt Userbot ===\n"
        f"  owner_id          : {config.OWNER_ID}\n"
        f"  vision votes       : {config.VOTES}\n"
        f"  webhook HMAC       : {hmac_state}\n"
        "  auth               : centralized core.auth (2s cache)\n"
        "  keypool            : 401/402 quarantine + 429 cooldown\n"
        "  retry              : exponential backoff + jitter\n"
        "  shutdown handlers   : SIGINT/SIGTERM graceful stop"
    )

async def main():

    try:
        config.validate_config()
    except ValueError as e:
        log.error("FATAL config error: %s", e)
        raise

    await setup_webhook()

    try:
        wordly.init_wordly()
    except Exception as e:
        log.warning("Could not pre-init Wordly index: %s", e)

    shutdown = asyncio.Event()
    _install_signal_handlers(shutdown)
    log.info(_startup_banner())

    task_telethon = asyncio.create_task(supervised_task("Telethon", run_telethon))
    task_web = asyncio.create_task(supervised_task("WebServer", run_web_server))
    task_watchdog = asyncio.create_task(supervised_task("Watchdog", telethon_watchdog))
    task_keepalive = asyncio.create_task(supervised_task("KeepAlive", keep_alive))
    tasks = [task_telethon, task_web, task_watchdog, task_keepalive]

    runner = asyncio.gather(*tasks)
    waiter = asyncio.ensure_future(shutdown.wait())

    try:
        await asyncio.wait({runner, waiter}, return_when=asyncio.FIRST_COMPLETED)
    except (asyncio.CancelledError, KeyboardInterrupt):
        log.info("Shutdown triggered via cancellation or interrupt.")
        shutdown.set()

    if shutdown.is_set():
        log.info("Shutdown signal received - stopping services gracefully...")

        task_keepalive.cancel()
        task_watchdog.cancel()

        task_telethon.cancel()

        if _web_server:
            _web_server.should_exit = True
        task_web.cancel()

        await asyncio.gather(task_keepalive, task_watchdog, task_telethon, task_web, return_exceptions=True)

        try:
            await asyncio.wait_for(client.disconnect(), timeout=5.0)
            log.info("Telethon client disconnected.")
        except Exception as e:
            log.warning("Disconnect during shutdown: %s", e)
        log.info("Graceful shutdown complete.")
        return

    try:
        await runner
    except Exception as e:
        log.error("Top-level supervisor exception: %s", e, exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Userbot stopped by user.")
