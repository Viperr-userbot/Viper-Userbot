import asyncio
import json
import logging
import re
import httpx

import config

log = logging.getLogger("hwh.tenthings")

SYSTEM_PROMPT = """You are an expert trivia and word-guessing AI for games like 10Things.
You are given a category/topic and a list of masked hints.
Hints use asterisks (*), underscores (_), dashes (-), or dots (.) to represent hidden letters.
Revealed letters and spaces are already provided.

CRITICAL INSTRUCTIONS:
1. Provide the exact real-world answer for every single item that fits the category and matches the revealed letters.
2. Return your answers as a clean numbered list in order:
1. Exact Answer
2. Exact Answer
...
Do NOT output extra commentary. Just the answers."""

PRIMARY_MODELS = [
    "free/gpt-6-luna",
    "free/deepseek-v4-pro-0813",
    "free/glm-5.3-flash",
    "free/mimo-v2.6-pro",
]

MASK_CHARS = "*⁎∗＊_-."
_MASK_CLASS = re.escape(MASK_CHARS)

ITEM_LINE_RE = re.compile(
    r"^\s*(?:\[|#|\()?\s*(\d+)\s*(?:\]|\)|\.|\:|\s*[\-–—]|\s+)\s*(.*)$",
    re.I
)

def normalize_hint(hint: str) -> str:
    """
    Normalizes hints with spaced characters like 'P * * * s' or 'A _ _ _ e'
    into compact masks like 'P***s' or 'A___e', while preserving multi-word spacing.
    """
    text = hint.strip()

    parts = re.split(r"\s{2,}|\s*[\/\|]\s*", text)
    norm_parts = []
    for p in parts:
        tokens = p.strip().split()

        if tokens and len(tokens) > 1 and all(len(c) == 1 for c in tokens):
            norm_parts.append("".join(tokens))
        else:
            norm_parts.append(p.strip())
    return " ".join(norm_parts)

def is_tenthings_message(text: str) -> bool:
    """
    Elastic detection: checks if the message is a 10Things game or trivia list.
    Supports numbers (1., 1:, 1), [1], #1), bullets, and any mask style (*, _, -, .).
    """
    if not text:
        return False
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return False

    if re.search(r"\b(?:10\s*things|tenthings|category\s*[:\-]|topic\s*[:\-])\b", text, re.I):
        return True

    numbered_items = 0
    masked_items = 0
    for line in lines:
        m = ITEM_LINE_RE.match(line)
        if m:
            numbered_items += 1
            content = m.group(2)
            if re.search(rf"[{_MASK_CLASS}•]{{1,}}", content) or re.search(rf"(?:\s*[{_MASK_CLASS}]){{2,}}", content):
                masked_items += 1
        elif re.match(r"^\s*[-•\*]\s+.*[\*\-_•]{1,}", line):
            numbered_items += 1
            masked_items += 1

    if numbered_items >= 2 and masked_items >= 1:
        return True
    if masked_items >= 2:
        return True

    return False

def parse_message(text: str) -> tuple[str, list[str]]:
    """
    Elastic parser: extracts category and item hints from any format.
    Strips 'Solved by' tags and cleans up emojis.
    """
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if not lines:
        return "", []

    cat = lines[0]
    cat = re.sub(r"^(?:Category|Topic|Theme|Title|Round\s*\d+)\s*[:\-–—]\s*", "", cat, flags=re.I).strip()
    if not cat or ITEM_LINE_RE.match(cat):
        cat = "General Trivia"

    items: list[str] = []
    for line in lines[1:]:
        m = ITEM_LINE_RE.match(line)
        if m:
            raw_item = m.group(2).strip()

            clean = re.sub(r"\s*\([^\)]*(?:found|solved|guessed|by)[^\)]*\)", "", raw_item, flags=re.I)
            clean = clean.replace("✅", "").strip()
            items.append(clean)
        else:
            m_bullet = re.match(r"^\s*[-•\*]\s+(.*)$", line)
            if m_bullet:
                raw_item = m_bullet.group(1).strip()
                clean = re.sub(r"\s*\([^\)]*(?:found|solved|guessed|by)[^\)]*\)", "", raw_item, flags=re.I)
                clean = clean.replace("✅", "").strip()
                items.append(clean)

    if not items:
        for line in lines:
            m = ITEM_LINE_RE.match(line)
            if m:
                items.append(m.group(2).strip())

    return cat, items

def hint_to_regex(hint: str) -> re.Pattern:
    """
    Converts a hint into a tolerant regex matching word characters, accents, and flexible punctuation.
    """
    norm = normalize_hint(hint)
    pattern = "^"
    for ch in norm:
        if ch in MASK_CHARS:
            pattern += r"[\w0-9]"
        elif ch.isspace():
            pattern += r"\s+"
        elif ch in r"[]{}()+?^$|\\":
            pattern += "\\" + ch
        elif ch in (":", ",", ";", "-", "–", "—", "'"):

            pattern += r"[\s" + re.escape(ch) + r"]*"
        else:
            pattern += re.escape(ch)
    pattern += "$"
    return re.compile(pattern, re.IGNORECASE)

def is_elastic_match(hint: str, candidate: str) -> bool:
    """
    Validates if candidate answer fits the hint with multiple layers of flexibility.
    """
    if not candidate:
        return False
    cand = candidate.strip()
    norm_h = normalize_hint(hint)

    h_chars = [c for c in norm_h if c.isalnum() or c in MASK_CHARS]
    c_chars = [c for c in cand if c.isalnum()]
    if len(h_chars) == len(c_chars):
        for h_ch, c_ch in zip(h_chars, c_chars):
            if h_ch not in MASK_CHARS and h_ch.upper() != c_ch.upper():
                return False

    try:
        rgx = hint_to_regex(hint)
        if rgx.match(cand):
            return True
    except Exception:
        pass

    h_alphas = [c.upper() for c in norm_h if c.isalnum() or c in MASK_CHARS]
    c_alphas = [c.upper() for c in cand if c.isalnum()]

    if abs(len(h_alphas) - len(c_alphas)) <= 1 and len(h_alphas) > 0:

        mismatches = 0
        min_len = min(len(h_alphas), len(c_alphas))
        for i in range(min_len):
            h_char = h_alphas[i]
            c_char = c_alphas[i]
            if h_char not in MASK_CHARS:
                if h_char != c_char:
                    mismatches += 1
        if mismatches == 0:
            return True

    h_words = norm_h.split()
    c_words = cand.split()
    if len(h_words) == len(c_words) and len(h_words) > 0:
        starts_match = all(
            (hw[0].isalnum() and cw[0].isalnum() and hw[0].upper() == cw[0].upper())
            for hw, cw in zip(h_words, c_words)
            if hw[0] not in ("*", "-", "_", ".")
        )
        if starts_match:
            return True

    return False

def extract_answers_elastic(content: str, expected_count: int = 10) -> list[str]:
    """
    Extracts answer items from ANY model response:
    JSON dict, JSON array, numbered list, markdown bullets, or line-by-line.
    """
    clean = re.sub(r"```(?:json)?", "", content).replace("```", "").strip()

    m_json = re.search(r"(\{.*\}|\[.*\])", clean, re.S)
    if m_json:
        try:
            json_str = re.sub(r",\s*([\]}])", r"\1", m_json.group(0))
            data = json.loads(json_str)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
            if isinstance(data, dict):
                if "answers" in data and isinstance(data["answers"], list):
                    return [str(x).strip() for x in data["answers"] if str(x).strip()]

                num_keys = sorted(
                    data.keys(),
                    key=lambda x: int(x) if str(x).isdigit() else 999
                )
                items = [str(data[k]).strip() for k in num_keys if str(data[k]).strip()]
                if items:
                    return items
        except Exception:
            pass

    numbered = re.findall(r"^\s*(?:#|\b)?\d+[\.:\)\-\]]\s*([^\n\r]+)", clean, re.M)
    if numbered:
        return [re.sub(r"^[\*\"']+|[\*\"']+$", "", x).strip() for x in numbered if x.strip()]

    bullets = re.findall(r"^\s*[-•\*]\s+([^\n\r]+)", clean, re.M)
    if bullets:
        return [re.sub(r"^[\*\"']+|[\*\"']+$", "", x).strip() for x in bullets if x.strip()]

    lines = [re.sub(r"^[\*\"'\d\.\:\-\)\s]+", "", l).strip() for l in clean.split("\n") if l.strip()]
    return [l for l in lines if l and not l.lower().startswith(("category", "here are", "answers"))]

async def _query_single_model(client: httpx.AsyncClient, model: str, prompt: str) -> list[str]:
    api_key = config.get_active_apinex_key()
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 800,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        r = await client.post(
            f"{config.APINEX_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=25.0
        )
        if r.status_code in (401, 402, 429):
            config.switch_to_next_apinex_key(api_key, r.status_code)
        r.raise_for_status()
        config.report_key_success(api_key)

        content = r.json()["choices"][0]["message"]["content"]
        return extract_answers_elastic(content)
    except Exception as e:
        log.warning("10Things query with model %s failed: %s", model, e)
    return []

async def solve_10things(category: str, items: list[str], blocked: set[str] | None = None) -> list[str]:
    """
    Solves 10Things items with elastic tolerance:
    - Automatically handles already solved items.
    - Queries models sequentially (not in parallel) to conserve tokens and API quota.
    - Validates answers using multi-tier elastic validation.
    - `blocked`: words already chosen/confirmed/rejected in this round (uppercase); never returned again.
    """
    try:
        from core.auth import verify_author_integrity
        verify_author_integrity()
    except Exception as e:
        log.error("Integrity check failed in 10Things solver: %s", e)
        return []

    if not items:
        return []
    blocked = {b.upper() for b in (blocked or set())}

    solved_indices = {}
    unsolved_items = []
    for idx, it in enumerate(items):
        clean_it = normalize_hint(it)
        has_mask = any(ch in MASK_CHARS for ch in clean_it)
        if not has_mask and len(clean_it) >= 2:
            solved_indices[idx] = it
        else:
            unsolved_items.append((idx, it))

    prompt = f"Category: {category or 'General Trivia'}\n\nHints to solve:\n"
    for idx, item in enumerate(items, 1):
        prompt += f"{idx}: {normalize_hint(item)}\n"

    ans_lists = []
    async with httpx.AsyncClient(timeout=30) as client:
        for model in PRIMARY_MODELS:
            res = await _query_single_model(client, model, prompt)
            if res:
                ans_lists.append(res)

                if len(res) >= len(items) * 0.7:
                    break

    final_answers: list[str] = []
    for i, item in enumerate(items):

        if i in solved_indices:
            final_answers.append(solved_indices[i])
            continue

        chosen = None
        for ans_list in ans_lists:
            if i < len(ans_list):
                cand = str(ans_list[i]).strip()
                if cand.upper() in blocked and is_elastic_match(item, cand):
                    continue
                if is_elastic_match(item, cand):
                    chosen = cand
                    break

        if not chosen:
            for ans_list in ans_lists:
                if i < len(ans_list):
                    cand = str(ans_list[i]).strip()
                    if cand and len(cand) >= 2 and cand.upper() not in blocked:
                        chosen = cand
                        break

        final_answers.append(chosen or item)

    return final_answers
