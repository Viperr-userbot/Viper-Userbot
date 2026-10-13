import asyncio
import io
import logging
import os
import re
import tempfile
import httpx
from PIL import Image

import config

log = logging.getLogger("hwh.kang")

_bot_username: str | None = None
EMOJI_REGEX = re.compile(r'[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff]', flags=re.UNICODE)

def extract_emojis(text: str | None) -> list[str]:
    """Extracts valid Unicode emojis from text, or returns default ['🤔']."""
    if not text:
        return ["🤔"]
    found = EMOJI_REGEX.findall(text)
    return found[:10] if found else ["🤔"]

async def get_bot_username() -> str:
    """Fetch and cache the Telegram bot's username using BOT_TOKEN."""
    global _bot_username
    if _bot_username:
        return _bot_username

    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        data = r.json()
        if data.get("ok"):
            _bot_username = data["result"]["username"]
            return _bot_username
        raise RuntimeError(f"Failed to fetch bot username: {data.get('description')}")

def detect_media_info(message) -> tuple[str | None, str | None, bool]:
    """
    Detects the sticker format type, default emoji, and if it's already a video sticker.
    Returns: (format_type, emoji, is_already_video_sticker)
    format_type: 'static' | 'animated' | 'video' | None
    """
    if not message:
        return None, None, False

    emoji = None
    is_animated_attr = False
    is_video_attr = False
    if getattr(message, "document", None) and getattr(message.document, "attributes", None):
        for attr in message.document.attributes:
            if hasattr(attr, "alt") and attr.alt:
                emoji = attr.alt
            attr_name = type(attr).__name__
            if "Animated" in attr_name or getattr(attr, "animated", False):
                is_animated_attr = True
            if "Video" in attr_name:
                is_video_attr = True

    mime = getattr(message.file, "mime_type", "") if hasattr(message, "file") and message.file else ""
    ext = getattr(message.file, "ext", "") if hasattr(message, "file") and message.file else ""

    if mime == "application/x-tgsticker" or ext == ".tgs":
        return "animated", emoji, False

    if mime == "video/webm" or ext == ".webm":
        return "video", emoji, True

    if (
        mime == "image/gif"
        or ext == ".gif"
        or getattr(message, "gif", None)
        or getattr(message, "video", None)
        or is_animated_attr
        or is_video_attr
        or mime.startswith("video/")
    ):
        return "video", emoji, False

    if (getattr(message, "sticker", None) and mime == "image/webp") or ext == ".webp":
        return "static", emoji, False

    if getattr(message, "photo", None):
        return "static", emoji, False

    if mime.startswith("image/"):
        return "static", emoji, False

    return None, None, False

def prepare_static_sticker(raw_bytes: bytes) -> bytes:
    """
    Resizes image so that the maximum dimension is 512px (maintaining aspect ratio),
    and converts to RGBA WEBP format.
    """
    img = Image.open(io.BytesIO(raw_bytes))
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    w, h = img.size
    if w >= h:
        new_w = 512
        new_h = max(1, int(round((512 * h) / w)))
    else:
        new_h = 512
        new_w = max(1, int(round((512 * w) / h)))

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=95)
    return buf.getvalue()

def prepare_video_sticker(raw_bytes: bytes, is_already_video_sticker: bool = False) -> bytes:
    """
    Converts GIF or video to VP9 WebM sticker format.
    - Max duration: Exactly up to 2.95s (Telegram's maximum allowed limit is 3.0s).
    - If video is longer than 3.0s (e.g. 4s-9s): Speeds up playback so the ENTIRE video action fits in 2.95s.
    - If video is very long (>9s): Speeds up by 3x (captures ~8.85s of action) into 2.95s.
    - If clip is short (<2.5s) or GIF: Loops smoothly to fill up to 2.95s so it's not super short.
    - Strictly ensures file size <= 256 KB (Telegram's max file size).
    - Codec: VP9 (libvpx-vp9), no audio, max dimension 512px.
    """
    import imageio_ffmpeg
    import subprocess

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    TARGET_DURATION = 2.95

    with tempfile.TemporaryDirectory() as td:
        in_file = os.path.join(td, "input.tmp")
        out_file = os.path.join(td, "output.webm")

        with open(in_file, "wb") as f:
            f.write(raw_bytes)

        p = subprocess.run([ffmpeg_exe, "-i", in_file], capture_output=True, text=True)
        w, h = 512, 512
        dur = 0.0
        for line in p.stderr.splitlines():
            if "Video:" in line:
                m = re.search(r"(\d{2,5})x(\d{2,5})", line)
                if m:
                    w, h = int(m.group(1)), int(m.group(2))
            if "Duration:" in line:
                m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", line)
                if m_dur:
                    dur = float(m_dur.group(1))*3600 + float(m_dur.group(2))*60 + float(m_dur.group(3))

        if is_already_video_sticker and len(raw_bytes) <= 256 * 1024 and 0 < dur <= 3.0:
            return raw_bytes

        scale_filter = "scale=512:-2" if w >= h else "scale=-2:512"

        use_loop = False
        if dur > 3.0:

            vf = f"{scale_filter},fps=30"
        elif 0 < dur < 2.5:

            use_loop = True
            vf = f"{scale_filter},fps=30"
        else:

            vf = f"{scale_filter},fps=30"

        cmd_base = [ffmpeg_exe, "-y"]
        if use_loop:
            cmd_base.extend(["-stream_loop", "-1"])
        cmd_base.extend([
            "-i", in_file,
            "-t", str(TARGET_DURATION),
            "-an",
            "-vf", vf,
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-b:v", "250k",
            "-maxrate", "300k",
            "-bufsize", "600k",
            "-crf", "32",
            out_file
        ])

        res = subprocess.run(cmd_base, capture_output=True)
        if res.returncode != 0:

            cmd_fallback = [ffmpeg_exe, "-y"]
            if use_loop:
                cmd_fallback.extend(["-stream_loop", "-1"])
            cmd_fallback.extend([
                "-i", in_file,
                "-t", str(TARGET_DURATION),
                "-an",
                "-vf", vf,
                "-c:v", "libvpx-vp9",
                "-b:v", "250k",
                "-maxrate", "300k",
                "-bufsize", "600k",
                "-crf", "32",
                out_file
            ])
            subprocess.run(cmd_fallback, capture_output=True, check=True)

        if os.path.exists(out_file) and os.path.getsize(out_file) > 250 * 1024:
            compressed_file = os.path.join(td, "compressed.webm")
            cmd_compress = [
                ffmpeg_exe, "-y",
                "-i", out_file,
                "-t", str(TARGET_DURATION),
                "-an",
                "-c:v", "libvpx-vp9",
                "-b:v", "160k",
                "-maxrate", "200k",
                "-bufsize", "400k",
                "-crf", "38",
                compressed_file
            ]
            comp_res = subprocess.run(cmd_compress, capture_output=True)
            if comp_res.returncode == 0 and os.path.exists(compressed_file):
                with open(compressed_file, "rb") as f:
                    return f.read()

        with open(out_file, "rb") as f:
            return f.read()

async def upload_sticker_file(client: httpx.AsyncClient, user_id: int, sticker_bytes: bytes, sticker_format: str) -> str:
    """Uploads the sticker file to Telegram via Bot API and returns file_id."""
    filenames = {
        "static": ("sticker.webp", "image/webp"),
        "animated": ("sticker.tgs", "application/x-tgsticker"),
        "video": ("sticker.webm", "video/webm"),
    }
    fname, mime = filenames.get(sticker_format, ("sticker.webp", "image/webp"))

    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/uploadStickerFile"
    r = await client.post(
        url,
        data={"user_id": user_id, "sticker_format": sticker_format},
        files={"sticker": (fname, sticker_bytes, mime)},
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"uploadStickerFile failed: {data.get('description')}")
    return data["result"]["file_id"]

async def add_or_create_sticker(
    user_id: int,
    user_display_name: str,
    sticker_bytes: bytes,
    sticker_format: str,
    emojis: list[str]
) -> tuple[str, str, bool]:
    """
    Adds sticker to user's pack or creates a new one if pack is full or doesn't exist.
    Returns: (pack_name, pack_title, is_first_kang)
    """
    bot_username = await get_bot_username()

    prefix = "k"
    title_suffix = ""
    if sticker_format == "animated":
        prefix = "ka"
        title_suffix = " Anim"
    elif sticker_format == "video":
        prefix = "kv"
        title_suffix = " Video"

    clean_name = re.sub(r"[^a-zA-Z0-9_]", "", user_display_name) or "User"

    async with httpx.AsyncClient(timeout=30) as http:
        pack_num = 1
        while True:
            pack_name = f"{prefix}_{user_id}_{pack_num}_by_{bot_username}"
            vol_str = f" Vol {pack_num}" if pack_num > 1 else ""
            pack_title = f"{clean_name}'s{title_suffix} Kang{vol_str}"

            get_res = await http.get(
                f"https://api.telegram.org/bot{config.BOT_TOKEN}/getStickerSet",
                params={"name": pack_name}
            )
            set_data = get_res.json()

            if not set_data.get("ok"):

                file_id = await upload_sticker_file(http, user_id, sticker_bytes, sticker_format)
                create_res = await http.post(
                    f"https://api.telegram.org/bot{config.BOT_TOKEN}/createNewStickerSet",
                    json={
                        "user_id": user_id,
                        "name": pack_name,
                        "title": pack_title,
                        "stickers": [{
                            "sticker": file_id,
                            "format": sticker_format,
                            "emoji_list": emojis
                        }]
                    }
                )
                c_data = create_res.json()
                if not c_data.get("ok"):
                    raise RuntimeError(f"createNewStickerSet failed: {c_data.get('description')}")
                return pack_name, pack_title, True

            stickers = set_data.get("result", {}).get("stickers", [])
            max_stickers = 50 if sticker_format == "video" else 120
            if len(stickers) >= max_stickers:
                pack_num += 1
                continue

            file_id = await upload_sticker_file(http, user_id, sticker_bytes, sticker_format)
            add_res = await http.post(
                f"https://api.telegram.org/bot{config.BOT_TOKEN}/addStickerToSet",
                json={
                    "user_id": user_id,
                    "name": pack_name,
                    "sticker": {
                        "sticker": file_id,
                        "format": sticker_format,
                        "emoji_list": emojis
                    }
                }
            )
            a_data = add_res.json()
            if a_data.get("ok"):
                return pack_name, pack_title, False

            err_desc = a_data.get("description", "")
            if "STICKERS_TOO_MUCH" in err_desc or "too much stickers" in err_desc.lower():
                pack_num += 1
                continue

            raise RuntimeError(f"addStickerToSet failed: {err_desc}")

async def notify_owner_new_pack(pack_name: str, pack_title: str):
    """
    Sends the sticker pack link to the owner's Telegram Bot DM on 1st kang.
    Only visible to the owner.
    """
    if not config.OWNER_ID:
        return

    msg = (
        f"🎉 <b>New Sticker Pack Created!</b>\n\n"
        f"📦 <b>Pack:</b> {pack_title}\n"
        f"🔗 <b>Link:</b> https://t.me/addstickers/{pack_name}"
    )

    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            await client.post(
                url,
                json={
                    "chat_id": config.OWNER_ID,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False
                }
            )
        except Exception as e:
            log.error(f"Failed to notify owner about new pack: {e}")

async def get_user_packs(user_id: int) -> list[dict]:
    """Returns list of active sticker packs for user."""
    bot_username = await get_bot_username()
    packs = []
    prefixes = [("static", "k", "Static"), ("animated", "ka", "Animated"), ("video", "kv", "Video")]
    async with httpx.AsyncClient(timeout=15) as http:
        for fmt, pfx, label in prefixes:
            pack_num = 1
            while True:
                pack_name = f"{pfx}_{user_id}_{pack_num}_by_{bot_username}"
                r = await http.get(
                    f"https://api.telegram.org/bot{config.BOT_TOKEN}/getStickerSet",
                    params={"name": pack_name}
                )
                data = r.json()
                if data.get("ok"):
                    res = data["result"]
                    packs.append({
                        "type": label,
                        "name": pack_name,
                        "title": res.get("title", pack_name),
                        "count": len(res.get("stickers", [])),
                        "link": f"https://t.me/addstickers/{pack_name}"
                    })
                    pack_num += 1
                else:
                    break
    return packs
