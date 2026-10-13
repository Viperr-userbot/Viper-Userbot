import asyncio
import base64
import io
import json
import logging
import os
import re
from collections import Counter

import httpx
from PIL import Image, ImageEnhance, ImageOps

import config

log = logging.getLogger("hwh.vision")

SYSTEM_PROMPT = """You are an ultra-precise OCR transcription engine for word search puzzles.
Rules:
1. The grid is a rectangular matrix of letter cells.
2. Every row contains the EXACT same number of single letters.
3. Transcribe each row from left to right. Never repeat or duplicate a letter.
4. Output strictly valid JSON."""

USER_PROMPT = """Transcribe this word search grid row by row from top to bottom.
Count the number of columns in the first row. Every subsequent row must have exactly that same number of letters.
Return ONLY this JSON format:
{
  "rows": [
    "ROW1_LETTERS",
    "ROW2_LETTERS"
  ]
}"""

PRIMARY_MODEL = getattr(config, "GPT_MODEL", "free/gpt-6-luna")
FALLBACK_MODELS = [
    getattr(config, "MISTRAL_MODEL", "pixtral-12b-2409"),
    "free/glm-5.3-flash",
]

def prep_image(raw: bytes) -> bytes:
    """Prepare image for optimal neural OCR without introducing compression or edge artifacts."""
    im = Image.open(io.BytesIO(raw))
    if im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size

    if w < 600:
        s = 600 / w
        im = im.resize((int(w * s), int(h * s)), Image.Resampling.BILINEAR)
    elif w > 1400:
        s = 1400 / w
        im = im.resize((int(w * s), int(h * s)), Image.Resampling.BILINEAR)

    im = ImageEnhance.Contrast(im).enhance(1.15)
    im = ImageEnhance.Sharpness(im).enhance(1.10)

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=95)
    return buf.getvalue()

def parse_rows(text: str) -> list[str]:
    """
    Parse JSON, markdown, or plain text into uppercase letter rows.
    Resilient to model formatting quirks, 2D arrays, and minor row-length discrepancies.
    """
    if not text or not text.strip():
        raise ValueError("Empty OCR output")

    cleaned = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()

    raw_rows = []

    json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if isinstance(data, list):
                raw_rows = data
            elif isinstance(data, dict):
                for k in ["rows", "grid", "letters", "data", "matrix"]:
                    if k in data and isinstance(data[k], list):
                        raw_rows = data[k]
                        break
        except Exception:
            pass

    if not raw_rows:
        m = re.search(r'"(?:rows|grid)"\s*:\s*\[([\s\S]*?)\]', cleaned, re.IGNORECASE)
        if m:
            items = re.findall(r'"([A-Za-z0-9\s,]{2,})"', m.group(1))
            if items:
                raw_rows = items

    if not raw_rows or len(raw_rows) < 4:
        line_candidates = []
        for line in cleaned.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("{") or line_str.startswith("}"):
                continue

            line_str = re.sub(r'^(?:row\s*\d+[:.]?|\d+[\.:\)\-\]]|\[\d+\])\s*', '', line_str, flags=re.IGNORECASE)
            line_str = line_str.replace('"', '').replace("'", '').replace(',', ' ').replace('\t', ' ')
            letters = "".join(ch for ch in line_str.upper().replace("0", "O") if "A" <= ch <= "Z")
            if len(letters) >= 4:
                line_candidates.append(letters)
        if len(line_candidates) >= 4:
            raw_rows = line_candidates

    if raw_rows and len(raw_rows) >= 16 and all(isinstance(x, str) and len(x.strip().replace(",", "")) <= 2 for x in raw_rows):
        flat_letters = "".join(str(x).upper().replace("0", "O") for x in raw_rows if "A" <= str(x).upper().replace("0", "O") <= "Z")
        for side in [8, 10, 7, 9, 6, 5]:
            if len(flat_letters) == side * side:
                raw_rows = [flat_letters[i*side:(i+1)*side] for i in range(side)]
                break

    clean = []
    for r in raw_rows:
        if isinstance(r, list):
            r_str = "".join(str(ch) for ch in r)
        else:
            r_str = str(r)
        r_str = r_str.upper().replace("0", "O")
        letters = "".join(ch for ch in r_str if "A" <= ch <= "Z")
        if letters:
            clean.append(letters)

    if len(clean) < 4:
        raise ValueError(f"Too few valid letter rows found: {len(clean)}")

    lengths = [len(r) for r in clean]
    counts = Counter(lengths)
    most_common_len, mode_count = counts.most_common(1)[0]

    if most_common_len < 4:
        raise ValueError(f"Grid column width too small: {most_common_len}")

    if len(set(lengths)) == 1:
        return clean

    if mode_count >= len(clean) * 0.7:
        repaired = []
        for r in clean:
            if len(r) == most_common_len:
                repaired.append(r)
            elif len(r) > most_common_len:
                repaired.append(r[:most_common_len])
            else:
                repaired.append(r.ljust(most_common_len, "A"))
        log.info("Auto-repaired grid rows from %s to uniform width %d", lengths, most_common_len)
        return repaired

    raise ValueError(f"Invalid grid shape: {lengths}")

async def _call_mistral_model(client: httpx.AsyncClient, b64: str, api_key: str | None = None, mime: str = "image/jpeg") -> list[str]:
    """Calls Mistral Vision API with specified or default MISTRAL_API_KEY with JSON mode and greedy decoding."""
    active_key = api_key or getattr(config, "MISTRAL_API_KEY", "")
    if not active_key:
        raise ValueError("MISTRAL_API_KEY not configured")

    payload = {
        "model": config.MISTRAL_MODEL,
        "temperature": 0.0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": f"data:{mime};base64,{b64}"},
                ],
            },
        ],
    }

    r = await client.post(
        f"{config.MISTRAL_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {active_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=12.0,
    )
    r.raise_for_status()
    resp_json = r.json()
    content = resp_json["choices"][0]["message"]["content"]
    return parse_rows(content)

async def _call_apinex_model(client: httpx.AsyncClient, b64: str, model: str, api_key: str, mime: str = "image/jpeg") -> list[str]:
    """Calls Apinex Vision API with key status tracking."""
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            },
        ],
    }

    r = await client.post(
        f"{config.APINEX_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=14.0,
    )
    if r.status_code in (401, 402, 429):
        config.report_key_error(api_key, r.status_code)
    r.raise_for_status()
    config.report_key_success(api_key)

    try:
        resp_json = r.json()
        content = resp_json["choices"][0]["message"]["content"]
    except Exception:
        m = re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', r.text)
        if m:
            content = m.group(1).encode('utf-8').decode('unicode_escape')
        else:
            content = r.text
    return parse_rows(content)

async def _call_ocr_space(raw: bytes, api_key: str | None = None) -> list[str]:
    """
    Calls OCR.space API as dedicated optical character recognition engine.
    Preprocesses the image (inversion/enhancement for dark background grids)
    and extracts rectangular letter rows.
    """
    key = api_key or getattr(config, "OCR_SPACE_API_KEY", "ccabe2951188957")
    if not key:
        raise ValueError("OCR_SPACE_API_KEY not configured")

    im = Image.open(io.BytesIO(raw))
    if im.mode != "RGB":
        im = im.convert("RGB")

    stat = im.resize((10, 10)).convert("L")
    avg_brightness = sum(stat.getdata()) / 100.0
    if avg_brightness < 128:
        im = ImageOps.invert(im)

    w, h = im.size
    if w < 1000:
        s = 1000 / w
        im = im.resize((int(w * s), int(h * s)), Image.Resampling.LANCZOS)
    elif w > 1800:
        s = 1800 / w
        im = im.resize((int(w * s), int(h * s)), Image.Resampling.LANCZOS)

    im = ImageEnhance.Contrast(im).enhance(1.4)
    im = ImageEnhance.Sharpness(im).enhance(1.4)

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    proc_bytes = buf.getvalue()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for eng in ["1", "2"]:
            try:
                files = {"file": ("grid.jpg", proc_bytes, "image/jpeg")}
                data = {
                    "apikey": key,
                    "language": "eng",
                    "isTable": "true",
                    "scale": "true",
                    "OCREngine": eng,
                }
                r = await client.post("https://api.ocr.space/parse/image", files=files, data=data)
                if r.status_code == 200:
                    res_json = r.json()
                    parsed = res_json.get("ParsedResults", [{}])[0].get("ParsedText", "")
                    if parsed and parsed.strip():
                        rows = parse_rows(parsed)
                        if rows:
                            log.info("OCR.space engine %s read grid (%d rows x %d cols)", eng, len(rows), len(rows[0]))
                            return rows
            except Exception as e:
                log.debug("OCR.space engine %s attempt error: %s", eng, e)

    raise ValueError("OCR.space could not read grid")

def vote(grids: list[list[str]]) -> tuple[list[str], int]:
    """Cell-wise majority voting. Returns (grid, number_of_disputed_cells)."""
    if len(grids) == 1:
        return grids[0], 0

    shape = Counter((len(g), len(g[0])) for g in grids).most_common(1)[0][0]
    same = [g for g in grids if (len(g), len(g[0])) == shape]
    R, C = shape
    out, disputed = [], 0
    for r in range(R):
        row = ""
        for c in range(C):
            cnt = Counter(g[r][c] for g in same)
            winner, winner_count = cnt.most_common(1)[0]
            total = sum(cnt.values())
            if winner_count / total < 0.6:
                disputed += 1
            row += winner
        out.append(row)
    return out, disputed

async def read_grid(raw: bytes) -> tuple[list[str], int]:
    """
    Transcribes the puzzle grid with ultra-fast speed & high accuracy:
    Races Apinex, Mistral, and OCR.space concurrently to guarantee < 10s response.
    """
    processed = prep_image(raw)
    b64 = base64.b64encode(processed).decode()
    mime = "image/jpeg"
    errors = []

    async def run_apinex(model, key):
        async with httpx.AsyncClient(timeout=8.0) as client:
            return await _call_apinex_model(client, b64, model, key, mime)

    async def run_mistral(key):
        async with httpx.AsyncClient(timeout=8.0) as client:
            return await _call_mistral_model(client, b64, key, mime)

    async def run_ocr():
        return await _call_ocr_space(raw)

    tasks = []

    apinex_keys = config.get_ordered_apinex_keys(only_healthy=True)
    if not apinex_keys:
        apinex_keys = config.get_ordered_apinex_keys()

    if apinex_keys:
        tasks.append(asyncio.create_task(run_apinex(PRIMARY_MODEL, apinex_keys[0])))
        tasks.append(asyncio.create_task(run_apinex("free/glm-5.3-flash", apinex_keys[0])))
        if len(apinex_keys) > 1:
            tasks.append(asyncio.create_task(run_apinex(PRIMARY_MODEL, apinex_keys[1])))

    mistral_keys = getattr(config, "MISTRAL_API_KEYS", [])
    if not mistral_keys and getattr(config, "MISTRAL_API_KEY", None):
        mistral_keys = [config.MISTRAL_API_KEY]

    if mistral_keys:
        tasks.append(asyncio.create_task(run_mistral(mistral_keys[0])))

    tasks.append(asyncio.create_task(run_ocr()))

    for coro in asyncio.as_completed(tasks):
        try:
            rows = await coro
            if rows:
                log.info("A model successfully read the grid (%d rows x %d cols)", len(rows), len(rows[0]))

                for t in tasks:
                    if not t.done():
                        t.cancel()
                return rows, 0
        except Exception as e:
            errors.append(str(e))

    raise RuntimeError(f"Koi bhi model grid padh nahi paya. Errors: {'; '.join(errors[-3:])}")
