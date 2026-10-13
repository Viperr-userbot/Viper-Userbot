import logging
import os
import re
import unicodedata
from collections import defaultdict
from typing import Any

import config

log = logging.getLogger("hwh.wordly")

COMMON_WORDS: set[str] = set()
ALL_WORDS: list[str] = []
ALL_WORDS_BY_LEN: dict[int, list[str]] = defaultdict(list)
ANAGRAM_MAP: dict[str, list[str]] = defaultdict(list)
_INITIALIZED: bool = False

def init_wordly(
    words_file: str = "words.txt",
    common_words_file: str = "common_words.txt"
) -> None:
    """
    Initializes dictionary and builds high-speed O(1) anagram index.
    Prioritizes common real-world English words for Wordly puzzles.
    """
    global COMMON_WORDS, ALL_WORDS, ALL_WORDS_BY_LEN, ANAGRAM_MAP, _INITIALIZED
    if _INITIALIZED:
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    cw_path = os.path.join(base_dir, common_words_file)
    w_path = os.path.join(base_dir, words_file)

    if os.path.exists(cw_path):
        try:
            with open(cw_path, "r", encoding="utf-8", errors="ignore") as f:
                COMMON_WORDS = {w.strip().lower() for w in f if w.strip()}
        except Exception as e:
            log.warning("Could not load common words: %s", e)

    if os.path.exists(w_path):
        try:
            with open(w_path, "r", encoding="utf-8", errors="ignore") as f:
                ALL_WORDS = [w.strip().lower() for w in f if w.strip()]
        except Exception as e:
            log.warning("Could not load words.txt: %s", e)

    anagram_dict = defaultdict(list)
    for w in ALL_WORDS:
        ALL_WORDS_BY_LEN[len(w)].append(w)
        key = "".join(sorted(w))
        anagram_dict[key].append(w)

    for key, words in anagram_dict.items():
        words.sort(key=lambda x: (0 if x in COMMON_WORDS else 1, x))

    ANAGRAM_MAP = anagram_dict
    _INITIALIZED = True
    log.info("Wordly index initialized with %d total words, %d anagram groups", len(ALL_WORDS), len(ANAGRAM_MAP))

def ensure_initialized() -> None:
    if not _INITIALIZED:
        init_wordly()

def extract_wordly_progress(text: str) -> tuple[int, int] | None:
    """Extracts (current_words_found, total_words) from Wordly message text, e.g. (17, 20), (5, 30), (12, 1000)."""
    if not text:
        return None
    t_norm = unicodedata.normalize("NFKD", text)
    prog_m = re.search(r"(?:Total\s+)?words?(?:\s+found)?\s*:\s*(\d+)\s*/\s*(\d+)", t_norm, re.I)
    if not prog_m:
        prog_m = re.search(r"(?:found|progress)\s*:\s*(\d+)\s*/\s*(\d+)", t_norm, re.I)
    if not prog_m:
        prog_m = re.search(r"\b(\d+)\s*/\s*(\d+)\b", t_norm)
    if prog_m:
        try:
            return (int(prog_m.group(1)), int(prog_m.group(2)))
        except Exception:
            pass
    return None

def is_wordly_game_over(text: str) -> bool:
    """Checks if message signals game over for Wordly."""
    if not text:
        return False
    t_clean = unicodedata.normalize("NFKD", text).lower()
    if "game over" in t_clean:
        return True
    if "/new2" in t_clean and ("scores" in t_clean or "winner" in t_clean or "points" in t_clean or "🏆" in text):
        return True
    prog = extract_wordly_progress(text)
    if prog and prog[1] > 0 and prog[0] >= prog[1]:
        return True
    return False

def is_wordly_message(text: str) -> bool:
    """
    Detects if a message is an active Wordly / Fill the blanks puzzle.
    Must contain:
    - 'Letters:' or similar letters indicator
    - A mask/blanks pattern (with underscores or placeholders)
    """
    if not text:
        return False
    if is_wordly_game_over(text):
        return False

    t_clean = unicodedata.normalize("NFKD", text)

    has_letters = bool(re.search(r"(?:Letters|letters)\s*:\s*[^\r\n]+", t_clean))

    has_mask = "_" in t_clean

    if has_letters and has_mask:
        return True

    if ("fill the blanks" in t_clean.lower() or "find the hidden word" in t_clean.lower()) and "_" in t_clean:
        return True

    return False

def parse_wordly_message(text: str) -> dict[str, Any] | None:
    """
    Extracts puzzle data from a Wordly message:
    - letters: list of uppercase letters, e.g. ['D', 'C', 'E', 'F', 'T', 'I', 'I']
    - mask: raw mask string, e.g. 'D _ _ _ _ _ t'
    - target_len: expected word length
    - progress: (current, total) e.g. (17, 20)
    - confirmation: previous round winner info if present
    """
    if not text or not is_wordly_message(text):
        return None

    t_norm = unicodedata.normalize("NFKD", text)

    let_m = re.search(r"(?:Letters|letters)\s*:\s*([^\r\n]+)", t_norm)
    if not let_m:
        return None

    letters_raw = let_m.group(1).strip()
    letters = re.findall(r"[a-zA-Z]", letters_raw)
    if len(letters) < 3:
        return None

    target_len = len(letters)

    lines = t_norm.splitlines()
    mask = None
    for idx, line in enumerate(lines):
        if re.search(r"(?:Letters|letters)\s*:", line):

            for next_line in lines[idx + 1 : idx + 4]:
                clean_l = next_line.strip()
                if "_" in clean_l:

                    mask_candidate = re.sub(r"^[^A-Za-z0-9_]+", "", clean_l).strip()
                    if mask_candidate and "_" in mask_candidate:
                        mask = mask_candidate
                        break
            if mask:
                break

    if not mask:

        for line in lines:
            clean_l = line.strip()
            if "_" in clean_l and not clean_l.startswith("http") and not clean_l.startswith("/"):
                mask_candidate = re.sub(r"^[^A-Za-z0-9_]+", "", clean_l).strip()
                if mask_candidate and "_" in mask_candidate:
                    mask = mask_candidate
                    break

    if not mask:
        return None

    progress = extract_wordly_progress(text)

    confirmation = parse_wordly_confirmation(text)

    if mask:
        mask_chars = re.findall(r"[a-zA-Z_]", re.sub(r"[\.\-\*]", "_", mask))
        if len(mask_chars) > 0:
            target_len = len(mask_chars)

    return {
        "letters": [c.upper() for c in letters],
        "mask": mask,
        "target_len": target_len,
        "progress": progress,
        "confirmation": confirmation,
    }

def mask_to_regex(mask_str: str, target_len: int) -> re.Pattern | None:
    """
    Converts a mask string (e.g. 'D _ _ _ _ _ t', '_ _ p _ _ _ e', '_ _ n _ _ e')
    into a flexible regex pattern.
    Handles exact token counts as well as spacing discrepancies.
    """
    if not mask_str:
        return None

    clean = re.sub(r"[\.\-\*]", "_", mask_str)
    chars = re.findall(r"[a-zA-Z_]", clean)
    if not chars:
        return None

    if len(chars) == target_len:
        pat = "^" + "".join("." if c == "_" else re.escape(c.lower()) for c in chars) + "$"
        return re.compile(pat, re.I)

    parts = ["^"]
    prev_was_under = False
    for c in chars:
        if c == "_":
            if not prev_was_under:
                parts.append(".*")
                prev_was_under = True
        else:
            parts.append(re.escape(c.lower()))
            prev_was_under = False
    parts.append("$")

    return re.compile("".join(parts), re.I)

def solve_wordly(
    letters: list[str],
    mask_str: str,
    max_cands: int = 5
) -> list[str]:
    """
    Solves Wordly puzzle extremely fast:
    1. Exact O(1) anagram lookup.
    2. Deep subset dictionary search filtered by mask and length.
    3. Relaxed search for fallback.
    """
    ensure_initialized()

    letters_clean = [c.lower() for c in letters if c.isalpha()]
    letters_len = len(letters_clean)

    mask_chars = re.findall(r"[a-zA-Z_]", re.sub(r"[\.\-\*]", "_", mask_str)) if mask_str else []
    mask_len = len(mask_chars)

    target_lens = {letters_len, mask_len}
    target_lens = {l for l in target_lens if l > 1}

    if not target_lens:
        return []

    matching: list[str] = []

    sorted_key = "".join(sorted(letters_clean))
    anagrams = ANAGRAM_MAP.get(sorted_key, [])

    regex = mask_to_regex(mask_str, letters_len)

    if anagrams:
        for w in anagrams:
            if regex and regex.match(w):
                matching.append(w.title())
            elif not regex:
                matching.append(w.title())

        if matching:
            return matching[:max_cands]

        matching = [w.title() for w in anagrams[:max_cands]]
        return matching

    candidates = []
    for t_len in target_lens:
        candidates.extend(ALL_WORDS_BY_LEN.get(t_len, []))

    resilient_regex = mask_to_regex(mask_str, -1)

    if resilient_regex:
        candidates = [w for w in candidates if resilient_regex.match(w)]

    if not candidates:
        return []

    if letters_clean:
        from collections import Counter
        letters_counts = Counter(letters_clean)

        valid_cands = []
        for w in candidates:
            w_counts = Counter(w)
            if all(letters_counts[c] >= count for c, count in w_counts.items()):
                valid_cands.append(w.title())

        if valid_cands:
            valid_cands.sort(key=lambda x: (0 if x.lower() in COMMON_WORDS else 1, x))
            return valid_cands[:max_cands]

    candidates.sort(key=lambda x: (0 if x in COMMON_WORDS else 1, x))
    return [w.title() for w in candidates[:max_cands]]

async def solve_wordly_with_ai_fallback(
    letters: list[str],
    mask_str: str,
    target_len: int
) -> list[str]:
    """
    Fallback using fast LLM when puzzle word is outside standard dictionary.
    """
    try:
        import gpt_service
        prompt = (
            f"You are playing Telegram Wordly. Unscramble these letters: {', '.join(letters)} "
            f"to form an exact English word of {target_len} letters that matches the mask '{mask_str}'. "
            f"Reply with ONLY the word, nothing else."
        )
        ans = await gpt_service.ask_ai(prompt)
        if ans:
            cleaned = re.sub(r"[^A-Za-z]", "", ans).strip().title()
            if len(cleaned) == target_len:
                return [cleaned]
    except Exception as e:
        log.warning("Wordly AI fallback error: %s", e)
    return []

def parse_wordly_confirmation(text: str) -> dict[str, Any] | None:
    """
    Parses confirmation from Wordly bot:
    e.g.:
    🏆 NYSA found 'Finding'
    ⏱ Time: 5.75s ❞
    👑 +14 pts!
    """
    if not text:
        return None

    t_norm = unicodedata.normalize("NFKD", text)

    m = re.search(r"(?:🏆\s*)?([^\n]+?)\s+found\s+[\'\"]?([A-Za-z]{2,25})[\'\"]?", t_norm, re.I)
    if not m:
        return None

    player = m.group(1).strip()
    word = m.group(2).strip().upper()

    points = None
    m_pts = re.search(r"\+(\d+)\s*pts?", t_norm, re.I)
    if m_pts:
        try:
            points = int(m_pts.group(1))
        except Exception:
            pass

    return {
        "player": player,
        "word": word,
        "points": points,
    }

def is_wordly_confirmation_for_me(
    text: str,
    my_id: int | None = None,
    my_username: str | None = None,
    my_first_name: str | None = None,
    my_last_name: str | None = None,
    target_word: str | None = None,
    msg_obj: Any | None = None,
    sent_msg_id: int | None = None
) -> tuple[bool, str | None]:
    """
    Checks if Wordly bot awarded points/found word to THIS userbot.
    Returns (is_me, word_found).
    """
    data = parse_wordly_confirmation(text)
    if not data or not data.get("player"):
        return False, None

    word = data.get("word")

    if msg_obj and sent_msg_id:
        reply_id = getattr(msg_obj, "reply_to_msg_id", None)
        if reply_id and reply_id == sent_msg_id:
            return True, word

    if msg_obj and hasattr(msg_obj, "entities") and msg_obj.entities:
        for ent in msg_obj.entities:
            ent_uid = getattr(ent, "user_id", None)
            if ent_uid and my_id and ent_uid == my_id:
                return True, word

    player_raw = data["player"]
    player_norm = unicodedata.normalize("NFKD", player_raw).lower().strip()
    player_clean = re.sub(r"[^a-z0-9]", "", player_norm)

    me_names = ["you"]
    if my_username:
        u_clean = my_username.lower().lstrip("@").strip()
        me_names.append(u_clean)
        me_names.append(f"@{u_clean}")
    if my_first_name:
        fn_clean = unicodedata.normalize("NFKD", my_first_name).lower().strip()
        me_names.append(fn_clean)
    if my_last_name:
        ln_clean = unicodedata.normalize("NFKD", my_last_name).lower().strip()
        me_names.append(ln_clean)
    if my_first_name and my_last_name:
        full_clean = unicodedata.normalize("NFKD", f"{my_first_name} {my_last_name}").lower().strip()
        me_names.append(full_clean)

    for name in me_names:
        n_clean = re.sub(r"[^a-z0-9]", "", name)
        if n_clean and (n_clean == player_clean or n_clean in player_clean or player_clean in n_clean):
            return True, word

    if target_word and word and word.upper() == target_word.upper():
        return True, word

    return False, None

def is_wordly_confirmation_for_other(
    text: str,
    my_id: int | None = None,
    my_username: str | None = None,
    my_first_name: str | None = None,
    my_last_name: str | None = None,
    target_word: str | None = None,
    msg_obj: Any | None = None,
    sent_msg_id: int | None = None
) -> tuple[bool, str | None, str | None]:
    """
    Checks if Wordly bot confirmed the word for ANOTHER player.
    Returns (is_other, word_found, player_name).
    """
    is_me, my_word = is_wordly_confirmation_for_me(
        text,
        my_id=my_id,
        my_username=my_username,
        my_first_name=my_first_name,
        my_last_name=my_last_name,
        target_word=target_word,
        msg_obj=msg_obj,
        sent_msg_id=sent_msg_id
    )
    if is_me:
        return False, None, None

    data = parse_wordly_confirmation(text)
    if data and data["player"] and data["word"]:
        return True, data["word"], data["player"]

    return False, None, None

