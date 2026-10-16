import os
import io
import re
import time
import asyncio
import base64
import collections
import hashlib
import hmac as _hmac
import html
import httpx
import logging
import secrets
import pyDes
from telethon import events, TelegramClient
from telethon.tl.types import (
    MessageEntityBold, MessageEntityItalic, MessageEntityCode,
    MessageEntityPre, MessageEntityTextUrl, MessageEntityUrl,
    MessageEntityMention, MessageEntityHashtag, MessageEntityBotCommand,
    MessageEntityStrike, MessageEntityUnderline, MessageEntitySpoiler,
    MessageEntityCustomEmoji, MessageEntityEmail, MessageEntityPhone,
    User, Channel, Chat, MessageEntityMentionName, DocumentAttributeAudio
)
from telethon.tl.functions.users import GetFullUserRequest
from core.auth import ctx_is_owner
import edge_tts
import config
import database
import rmbg
import txt_sticker
import clone_profile
import waifu

log = logging.getLogger("hwh.plugins")

_recent_messages: dict[int, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=100))
_deleted_messages: dict[int, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=30))

def format_duration(seconds: float) -> str:
    """Formats seconds into human-readable duration like '15s', '5m 12s', '2h 15m'."""
    secs = int(max(0, seconds))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    rem_secs = secs % 60
    if mins < 60:
        return f"{mins}m {rem_secs}s" if rem_secs else f"{mins}m"
    hours = mins // 60
    rem_mins = mins % 60
    if hours < 24:
        return f"{hours}h {rem_mins}m" if rem_mins else f"{hours}h"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"

_afk_last_replied: dict[int, float] = {}

QUOTE_URL = "https://quote.nubcoders.com/generate"

async def get_base64_media(client, message):
    if not message.media:
        return None
    try:
        data = await client.download_media(message, file=bytes)
        if data:
            return base64.b64encode(data).decode('utf-8')
    except Exception as e:
        log.warning(f"Error downloading media for quote: {e}")
    return None

def convert_entities(entities):
    res = []
    if not entities:
        return res
    mapping = {
        MessageEntityBold: "bold",
        MessageEntityItalic: "italic",
        MessageEntityCode: "code",
        MessageEntityPre: "pre",
        MessageEntityTextUrl: "text_link",
        MessageEntityUrl: "url",
        MessageEntityMention: "mention",
        MessageEntityHashtag: "hashtag",
        MessageEntityBotCommand: "bot_command",
        MessageEntityStrike: "strikethrough",
        MessageEntityUnderline: "underline",
        MessageEntitySpoiler: "spoiler",
        MessageEntityCustomEmoji: "custom_emoji",
        MessageEntityEmail: "email",
        MessageEntityPhone: "phone_number"
    }
    for e in entities:
        t = mapping.get(type(e), "text")
        d = {"type": t, "offset": e.offset, "length": e.length}
        if hasattr(e, "url"):
            d["url"] = e.url
        if hasattr(e, "document_id"):
            d["custom_emoji_id"] = str(e.document_id)
        res.append(d)
    return res

async def build_user_info(client, user):
    if not user:
        return None
    first = getattr(user, 'first_name', None) or getattr(user, 'title', None) or "User"
    last = getattr(user, 'last_name', None) or ""
    name = f"{first} {last}".strip()
    info = {
        "id": user.id,
        "first_name": first,
        "last_name": last,
        "name": name,
    }
    if getattr(user, 'username', None):
        info["username"] = user.username

    try:
        photo_bytes = await client.download_profile_photo(user, file=bytes, download_big=False)
        if photo_bytes:
            info["photo"] = {"base64": base64.b64encode(photo_bytes).decode('utf-8')}
    except Exception:
        pass

    return info

async def generate_quote(client, message, custom_text=None, include_reply=False, color="#1b1429"):
    target = message
    if custom_text:
        text = custom_text
        entities = []
        media = None
    else:
        text = target.raw_text or ""
        entities = convert_entities(target.entities)
        media = await get_base64_media(client, target) if target.media else None

    if not text.strip() and not media:
        text = " "

    user_info = await build_user_info(client, getattr(target, 'sender', None) or target.chat)
    if not user_info:
        user_info = {"id": 1, "first_name": "Unknown", "name": "Unknown"}

    msg_data = {
        "text": text,
        "entities": entities,
        "from": user_info,
        "chatId": getattr(target.chat, 'id', 0),
        "avatar": True
    }
    if media:
        msg_data["media"] = {"base64": media}

    if include_reply and target.reply_to_msg_id:
        reply_msg = await target.get_reply_message()
        if reply_msg:
            r_user = getattr(reply_msg, 'sender', None) or reply_msg.chat
            r_info = await build_user_info(client, r_user)
            if r_info:
                r_text = reply_msg.raw_text or " "
                if not r_text.strip() and reply_msg.media:
                    r_text = " "
                msg_data["replyMessage"] = {
                    "name": r_info["name"],
                    "text": r_text[:100],
                    "entities": convert_entities(reply_msg.entities),
                    "chatId": getattr(reply_msg.chat, 'id', 0),
                    "from": r_info
                }

    payload = {
        "type": "quote",
        "format": "webp",
        "backgroundColor": color,
        "messages": [msg_data]
    }

    async with httpx.AsyncClient(timeout=15.0) as http:
        res = await http.post(QUOTE_URL, json=payload)
        res.raise_for_status()
        data = res.json()
        if data.get("ok") and data.get("result", {}).get("image"):
            b64 = data["result"]["image"]
            b64 = b64.replace("data:image/webp;base64,", "")
            return base64.b64decode(b64)
    return None

async def reply_or_edit(event, text: str, **kwargs):
    if event.out:
        try:
            return await event.edit(text, **kwargs)
        except Exception:
            return await event.reply(text, **kwargs)
    else:
        return await event.reply(text, **kwargs)

_chat_passkeys: dict[int, str] = {}

_GLOBAL_PASSKEY = os.getenv("ENCRYPTION_KEY", "")

_ENC_BLOCK_SIZE = 16
_ENC_KEY_ITERATIONS = 100_000
_ENC_MAGIC = "🔒"
_ENC_VERSION = "v1"

def _derive_key(passkey: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 key derivation — 256-bit key from passkey + salt."""
    return hashlib.pbkdf2_hmac("sha256", passkey.encode("utf-8"), salt, _ENC_KEY_ITERATIONS)

def _xor_blocks(data: bytes, key: bytes) -> bytes:
    """XOR cipher with key-stream expansion (repeating key across blocks)."""
    key_len = len(key)
    return bytes(data[i] ^ key[i % key_len] for i in range(len(data)))

def _pkcs7_pad(data: bytes, block_size: int = _ENC_BLOCK_SIZE) -> bytes:
    """PKCS7 padding."""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)

def _pkcs7_unpad(data: bytes) -> bytes:
    """PKCS7 unpadding with validation."""
    if not data:
        raise ValueError("Empty data")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > _ENC_BLOCK_SIZE:
        raise ValueError("Invalid padding")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("Corrupt padding")
    return data[:-pad_len]

def _cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """CBC-mode encryption using XOR block cipher."""
    padded = _pkcs7_pad(plaintext)
    encrypted = b""
    prev_block = iv
    for i in range(0, len(padded), _ENC_BLOCK_SIZE):
        block = padded[i:i + _ENC_BLOCK_SIZE]

        chained = bytes(block[j] ^ prev_block[j] for j in range(_ENC_BLOCK_SIZE))
        cipher_block = _xor_blocks(chained, key)
        encrypted += cipher_block
        prev_block = cipher_block
    return encrypted

def _cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """CBC-mode decryption using XOR block cipher."""
    if len(ciphertext) % _ENC_BLOCK_SIZE != 0:
        raise ValueError("Invalid ciphertext length")
    decrypted = b""
    prev_block = iv
    for i in range(0, len(ciphertext), _ENC_BLOCK_SIZE):
        block = ciphertext[i:i + _ENC_BLOCK_SIZE]
        decrypted_block = _xor_blocks(block, key)

        plain_block = bytes(decrypted_block[j] ^ prev_block[j] for j in range(_ENC_BLOCK_SIZE))
        decrypted += plain_block
        prev_block = block
    return _pkcs7_unpad(decrypted)

def encrypt_message(plaintext: str, passkey: str) -> str:
    """
    Encrypts a message with passkey.
    Output format: 🔒v1:<salt_b64>:<iv_b64>:<ciphertext_b64>:<hmac_b64>
    """
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(_ENC_BLOCK_SIZE)
    key = _derive_key(passkey, salt)

    ciphertext = _cbc_encrypt(plaintext.encode("utf-8"), key, iv)

    mac_data = salt + iv + ciphertext
    mac = _hmac.new(key, mac_data, hashlib.sha256).digest()

    parts = [
        _ENC_MAGIC + _ENC_VERSION,
        base64.b64encode(salt).decode(),
        base64.b64encode(iv).decode(),
        base64.b64encode(ciphertext).decode(),
        base64.b64encode(mac).decode(),
    ]
    return ":".join(parts)

def decrypt_message(encrypted_str: str, passkey: str) -> str:
    """
    Decrypts a 🔒v1:... encrypted message with passkey.
    Raises ValueError on wrong key or tampered data.
    """
    parts = encrypted_str.split(":")
    if len(parts) != 5 or not parts[0].startswith(_ENC_MAGIC):
        raise ValueError("Invalid encrypted message format")

    version = parts[0].replace(_ENC_MAGIC, "")
    if version != _ENC_VERSION:
        raise ValueError(f"Unsupported encryption version: {version}")

    salt = base64.b64decode(parts[1])
    iv = base64.b64decode(parts[2])
    ciphertext = base64.b64decode(parts[3])
    mac_received = base64.b64decode(parts[4])

    key = _derive_key(passkey, salt)

    mac_data = salt + iv + ciphertext
    mac_expected = _hmac.new(key, mac_data, hashlib.sha256).digest()
    if not _hmac.compare_digest(mac_received, mac_expected):
        raise ValueError("❌ Wrong passkey or message tampered!")

    plaintext_bytes = _cbc_decrypt(ciphertext, key, iv)
    return plaintext_bytes.decode("utf-8")

def is_encrypted_message(text: str) -> bool:
    """Check if a message is an encrypted 🔒v1:... string."""
    return bool(text and text.startswith(_ENC_MAGIC + _ENC_VERSION + ":"))

def get_chat_passkey(chat_id: int) -> str | None:
    """Get the passkey for a chat (per-chat > global > None)."""
    return _chat_passkeys.get(chat_id) or _GLOBAL_PASSKEY or None

def set_chat_passkey(chat_id: int, passkey: str) -> None:
    """Set encryption passkey for a specific chat."""
    _chat_passkeys[chat_id] = passkey

def clear_chat_passkey(chat_id: int) -> None:
    """Remove encryption passkey for a chat."""
    _chat_passkeys.pop(chat_id, None)


LANG_CODE_MAP = {
    "hindi": "HI", "hi": "HI",
    "english": "EN", "en": "EN",
    "bengali": "BN", "bn": "BN",
    "telugu": "TE", "te": "TE",
    "marathi": "MR", "mr": "MR",
    "tamil": "TA", "ta": "TA",
    "urdu": "UR", "ur": "UR",
    "gujarati": "GU", "gu": "GU",
    "kannada": "KN", "kn": "KN",
    "malayalam": "ML", "ml": "ML",
    "punjabi": "PA", "pa": "PA",
    "spanish": "ES", "es": "ES",
    "french": "FR", "fr": "FR",
    "german": "DE", "de": "DE",
    "russian": "RU", "ru": "RU",
    "japanese": "JA", "ja": "JA",
    "chinese": "ZH", "zh": "ZH",
    "arabic": "AR", "ar": "AR",
    "portuguese": "PT", "pt": "PT",
    "italian": "IT", "it": "IT",
    "korean": "KO", "ko": "KO",
}


def normalize_lang_code(raw: str) -> str:
    cleaned = (raw or "").strip().lower()
    return LANG_CODE_MAP.get(cleaned, raw.upper() if raw else "HI")


async def call_apinex_tts(text: str, model: str = "aura-2-thalia-en") -> bytes | None:
    """Synthesizes text into MP3 audio bytes using APInex TTS (/v1/tools/voice/tts)."""
    keys = config.get_ordered_apinex_keys()
    if not keys:
        log.warning("No Apinex keys configured for TTS.")
        return None

    base_url = config.APINEX_BASE_URL.rstrip("/")
    endpoint = f"{base_url}/tools/voice/tts"
    payload = {
        "text": text,
        "model": model,
        "encoding": "mp3",
        "response_format": "json"
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        for key in keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            try:
                res = await client.post(endpoint, json=payload, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    b64 = data.get("audio_base64")
                    if b64:
                        config.report_key_success(key)
                        return base64.b64decode(b64)
                else:
                    log.warning(f"Apinex TTS returned HTTP {res.status_code}: {res.text[:100]}")
                    config.report_key_error(key, res.status_code)
            except Exception as e:
                log.warning(f"Apinex TTS request failed with key {key[:8]}...: {e}")
                config.report_key_error(key, 0)
    return None


async def call_apinex_stt(audio_bytes: bytes, mime_type: str = "audio/ogg", language: str | None = None) -> dict | None:
    """Transcribes audio binary into text using APInex STT (/v1/tools/voice/stt)."""
    keys = config.get_ordered_apinex_keys()
    if not keys:
        log.warning("No Apinex keys configured for STT.")
        return None

    base_url = config.APINEX_BASE_URL.rstrip("/")
    endpoint = f"{base_url}/tools/voice/stt"
    if language:
        endpoint += f"?language={language.lower()}"

    async with httpx.AsyncClient(timeout=35.0) as client:
        for key in keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": mime_type
            }
            try:
                res = await client.post(endpoint, content=audio_bytes, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    channels = data.get("results", {}).get("channels", [])
                    if channels:
                        alts = channels[0].get("alternatives", [])
                        transcript = alts[0].get("transcript", "").strip() if alts else ""
                        conf = alts[0].get("confidence", 1.0) if alts else 1.0
                        det_lang = channels[0].get("detected_language", language or "auto")
                        dur = data.get("metadata", {}).get("duration", 0.0)
                        config.report_key_success(key)
                        return {
                            "transcript": transcript,
                            "confidence": conf,
                            "language": det_lang,
                            "duration": dur,
                            "raw": data
                        }
                else:
                    log.warning(f"Apinex STT returned HTTP {res.status_code}: {res.text[:100]}")
                    config.report_key_error(key, res.status_code)
            except Exception as e:
                log.warning(f"Apinex STT request failed with key {key[:8]}...: {e}")
                config.report_key_error(key, 0)
    return None


async def call_apinex_translator(text: str | list[str], target_lang: str = "HI", source_lang: str | None = None) -> dict | None:
    """Translates text strings using APInex Translator (/v1/tools/translator)."""
    keys = config.get_ordered_apinex_keys()
    if not keys:
        log.warning("No Apinex keys configured for Translator.")
        return None

    base_url = config.APINEX_BASE_URL.rstrip("/")
    endpoint = f"{base_url}/tools/translator"
    payload = {
        "text": [text] if isinstance(text, str) else text,
        "target_lang": target_lang.upper()
    }
    if source_lang:
        payload["source_lang"] = source_lang.upper()

    async with httpx.AsyncClient(timeout=20.0) as client:
        for key in keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            try:
                res = await client.post(endpoint, json=payload, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    translations = data.get("translations", [])
                    if translations:
                        trans_text = translations[0].get("text", "")
                        det_source = translations[0].get("detected_source_language", source_lang or "auto")
                        config.report_key_success(key)
                        return {
                            "translated_text": trans_text,
                            "detected_source": det_source,
                            "translations": translations,
                            "raw": data
                        }
                else:
                    log.warning(f"Apinex Translator returned HTTP {res.status_code}: {res.text[:100]}")
                    config.report_key_error(key, res.status_code)
            except Exception as e:
                log.warning(f"Apinex Translator request failed with key {key[:8]}...: {e}")
                config.report_key_error(key, 0)
    return None


def register(client: TelegramClient, is_authorized, is_owner=None):
    def check_is_owner(event) -> bool:
        if getattr(event, "out", False):
            return True
        sender_id = getattr(event, "sender_id", None)
        if not sender_id and hasattr(event, "message") and event.message:
            sender_id = getattr(event.message, "sender_id", None)
        # Sudo users are strictly forbidden from owner-only actions like clone
        if sender_id and database.is_sudo_user(sender_id):
            return False
        if is_owner is not None:
            return is_owner(event)
        return ctx_is_owner(sender_id, getattr(event, "chat_id", None))

    @client.on(events.NewMessage(pattern=r"^\.(q|qf|qtrf|qr)(?:\s+(.*))?$"))
    async def quote_cmd(event):
        if not is_authorized(event):
            return
        cmd = event.pattern_match.group(1).lower()
        args = event.pattern_match.group(2) or ""

        replied = await event.get_reply_message()
        if not replied:
            await reply_or_edit(event, "Reply to a message to quote it.")
            return

        include_reply = (cmd in ["qr", "qtrf"])
        force_custom = (cmd in ["qf", "qtrf"])

        color = "#1b1429"
        custom_text = args
        parts = args.split()
        if parts:
            if parts[0].startswith("#") or parts[0] in ["red", "blue", "green", "black", "white", "pink", "cyan"]:
                color = parts[0]
                custom_text = " ".join(parts[1:])

        if force_custom and not custom_text.strip():
            await reply_or_edit(event, "Custom text is required for qf/qtrf.")
            return

        status_msg = await reply_or_edit(event, "🐾 Generating quote...")
        try:
            image_bytes = await generate_quote(client, replied, custom_text=custom_text if force_custom else None, include_reply=include_reply, color=color)
            if image_bytes:
                img_io = io.BytesIO(image_bytes)
                img_io.name = "quote.webp"
                await client.send_file(event.chat_id, img_io, reply_to=replied)
                if event.out:
                    try: await event.delete()
                    except Exception: pass
                elif status_msg:
                    try: await status_msg.delete()
                    except Exception: pass
            else:
                await reply_or_edit(event, "❌ Failed to generate quote.")
        except Exception as e:
            log.error(f"Quote error: {e}")
            await reply_or_edit(event, f"❌ Error: {e}")

    # =========================================================================
    # APINEX VOICE & TRANSLATION COMMANDS (.tts, .stt, .tr)
    # =========================================================================

    @client.on(events.NewMessage(pattern=r"^\.tts(?:\s+(.*))?$"))
    async def tts_cmd(event):
        if not is_authorized(event):
            return
        try:
            args = (event.pattern_match.group(1) or "").strip()
            replied = await event.get_reply_message()

            model = "aura-2-thalia-en"
            text = args
            parts = args.split(maxsplit=1)
            if parts and (parts[0].startswith("aura-") or parts[0] in ["thalia", "andromeda", "estrella", "zeus"]):
                model = parts[0] if parts[0].startswith("aura-") else f"aura-2-{parts[0]}-en"
                text = parts[1] if len(parts) > 1 else ""

            if not text and replied:
                text = replied.raw_text or ""

            if not text:
                await reply_or_edit(event, "ℹ️ <b>Usage:</b> <code>.tts &lt;text&gt;</code> ya kisi message par reply karke <code>.tts</code> likhein.", parse_mode="html")
                return

            status_msg = await reply_or_edit(event, f"🗣️ <i>Generating AI speech ({model})...</i>", parse_mode="html")

            audio_data = await call_apinex_tts(text, model=model)
            if not audio_data:
                # Fallback to edge_tts
                try:
                    communicate = edge_tts.Communicate(text, "hi-IN-SwaraNeural")
                    edge_bytes = b""
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            edge_bytes += chunk["data"]
                    if edge_bytes:
                        audio_data = edge_bytes
                except Exception as e_tts:
                    log.warning(f"edge_tts fallback failed: {e_tts}")

            if audio_data:
                bio = io.BytesIO(audio_data)
                bio.name = "voice.mp3"
                await client.send_file(
                    event.chat_id,
                    bio,
                    voice_note=True,
                    reply_to=replied or event.id
                )
                if event.out:
                    try: await event.delete()
                    except Exception: pass
                elif status_msg:
                    try: await status_msg.delete()
                    except Exception: pass
            else:
                await reply_or_edit(event, "❌ <b>TTS failed:</b> APInex aur edge-tts dono se audio generate nahi ho saka.", parse_mode="html")
        except Exception as e:
            log.error(f"TTS error: {e}", exc_info=True)
            await reply_or_edit(event, f"❌ Error: <code>{html.escape(str(e))}</code>", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\.stt(?:\s+(.*))?$"))
    async def stt_cmd(event):
        if not is_authorized(event):
            return
        try:
            args = (event.pattern_match.group(1) or "").strip()
            replied = await event.get_reply_message()
            if not replied or not (replied.voice or replied.audio or replied.video_note or replied.video or replied.media):
                await reply_or_edit(
                    event,
                    "ℹ️ <b>Usage:</b> Kisi voice note, audio ya video message par reply karke <code>.stt [lang]</code> likhein.",
                    parse_mode="html"
                )
                return

            status_msg = await reply_or_edit(event, "🎙️ <i>Downloading & transcribing audio...</i>", parse_mode="html")
            audio_bytes = await client.download_media(replied, file=bytes)
            if not audio_bytes:
                await reply_or_edit(event, "❌ Audio download nahi ho saka.", parse_mode="html")
                return

            mime = "audio/ogg"
            if getattr(replied, "audio", None):
                mime = getattr(replied.file, "mime_type", "audio/mpeg") or "audio/mpeg"
            elif getattr(replied, "voice", None):
                mime = "audio/ogg"

            target_lang = normalize_lang_code(args) if args else None

            res = await call_apinex_stt(audio_bytes, mime_type=mime, language=target_lang)
            if not res or not res.get("transcript"):
                await reply_or_edit(event, "❌ <b>STT failed:</b> Audio transcribe nahi ho saka ya koi speech detect nahi hui.", parse_mode="html")
                return

            transcript = res["transcript"]
            det_lang = (res.get("language") or "auto").upper()
            conf = res.get("confidence", 1.0)
            dur = res.get("duration", 0.0)
            dur_str = f"{dur:.1f}s" if dur else "N/A"

            output = (
                "🎙️ <b>Speech to Text</b>\n\n"
                f"• <b>Language:</b> <code>{html.escape(det_lang)}</code>\n"
                f"• <b>Confidence:</b> <code>{conf * 100:.1f}%</code>\n"
                f"• <b>Duration:</b> <code>{dur_str}</code>\n\n"
                f"📝 <b>Transcript:</b>\n"
                f"<blockquote>{html.escape(transcript)}</blockquote>"
            )
            await reply_or_edit(event, output, parse_mode="html")
        except Exception as e:
            log.error(f"STT error: {e}", exc_info=True)
            await reply_or_edit(event, f"❌ Error: <code>{html.escape(str(e))}</code>", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\.tr(?:\s+(.*))?$"))
    async def tr_cmd(event):
        if not is_authorized(event):
            return
        try:
            args = (event.pattern_match.group(1) or "").strip()
            replied = await event.get_reply_message()

            if not args and not replied:
                await reply_or_edit(
                    event,
                    "ℹ️ <b>Usage:</b> <code>.tr [lang] &lt;text&gt;</code> ya kisi message par reply karke <code>.tr [lang]</code> likhein.",
                    parse_mode="html"
                )
                return

            target_lang = "HI"
            text = ""
            parts = args.split(maxsplit=1) if args else []

            if parts:
                candidate = parts[0].strip().lower()
                if candidate in LANG_CODE_MAP or (len(candidate) in [2, 5] and candidate.isalpha()):
                    target_lang = normalize_lang_code(candidate)
                    text = parts[1].strip() if len(parts) > 1 else ""
                else:
                    text = args

            transcript_info = None

            # If no direct text, check replied message
            if not text and replied:
                if replied.voice or replied.audio or replied.video_note:
                    status_msg = await reply_or_edit(event, "🎙️ <i>Transcribing voice note with APInex STT...</i>", parse_mode="html")
                    audio_bytes = await client.download_media(replied, file=bytes)
                    if not audio_bytes:
                        await reply_or_edit(event, "❌ Audio download nahi ho saka.", parse_mode="html")
                        return

                    mime = "audio/ogg" if replied.voice else "audio/mpeg"
                    stt_res = await call_apinex_stt(audio_bytes, mime_type=mime)
                    if not stt_res or not stt_res.get("transcript"):
                        await reply_or_edit(event, "❌ Audio transcribe nahi ho saka.", parse_mode="html")
                        return
                    text = stt_res["transcript"]
                    transcript_info = stt_res
                else:
                    text = replied.raw_text or ""

            if not text:
                await reply_or_edit(event, "❌ Translate karne ke liye koi text nahi mila.", parse_mode="html")
                return

            status_msg = await reply_or_edit(event, f"🌐 <i>Translating to {html.escape(target_lang)}...</i>", parse_mode="html")

            # Call APInex Translator API
            tr_res = await call_apinex_translator(text, target_lang=target_lang)
            translated_text = ""
            detected_src = "auto"
            if tr_res and tr_res.get("translated_text"):
                translated_text = tr_res["translated_text"]
                detected_src = (tr_res.get("detected_source") or "auto").upper()
            else:
                # LLM / gpt fallback
                try:
                    from gpt_service import query_gpt
                    prompt = f"Translate the following text to {target_lang}. Return ONLY the translated text without extra explanation:\n\n{text}"
                    translated_text = (await query_gpt(prompt=prompt)).strip()
                    detected_src = "AUTO"
                except Exception as ex:
                    log.warning(f"GPT fallback translation failed: {ex}")

            if not translated_text:
                await reply_or_edit(event, "❌ <b>Translation failed:</b> Translation response nahi mila.", parse_mode="html")
                return

            header = f"🌐 <b>Translation</b> (<code>{html.escape(detected_src)} ➔ {html.escape(target_lang.upper())}</code>)\n\n"
            if transcript_info:
                header = (
                    f"🎙️ <b>Transcribed Speech:</b>\n"
                    f"<blockquote>{html.escape(text)}</blockquote>\n\n"
                    + header
                )

            final_output = f"{header}<blockquote>{html.escape(translated_text)}</blockquote>"
            await reply_or_edit(event, final_output, parse_mode="html")
        except Exception as e:
            log.error(f"Translate error: {e}", exc_info=True)
            await reply_or_edit(event, f"❌ Error: <code>{html.escape(str(e))}</code>", parse_mode="html")

    # =========================================================================
    # RMBG - AI BACKGROUND REMOVAL (.rmbg, .removebg, .bg)
    # =========================================================================
    @client.on(events.NewMessage(pattern=r"^\.(?:rmbg|removebg|bg)(?:\s+(.*))?$"))
    async def rmbg_cmd(event):
        if not is_authorized(event):
            return

        args = (event.pattern_match.group(1) or "").strip().lower()
        as_png = any(flag in args.split() for flag in ["-p", "-png", "png", "doc", "-doc"])
        as_sticker = not as_png

        # Determine target message with media (replied message takes priority, then event message)
        target_msg = await event.get_reply_message()
        if not target_msg or not target_msg.media:
            if event.media:
                target_msg = event
            else:
                await reply_or_edit(
                    event,
                    "ℹ️ <b>Usage:</b> Reply to any photo, sticker, or image with <code>.rmbg</code>\n"
                    "• Sends directly as transparent Telegram Sticker.\n"
                    "• Add <code>-png</code> to receive as a transparent PNG file.",
                    parse_mode="html"
                )
                return

        # Check for animated/video media which cannot be processed as static images
        is_animated_or_video = False
        mime = getattr(target_msg.file, "mime_type", "") if hasattr(target_msg, "file") and target_msg.file else ""
        ext = getattr(target_msg.file, "ext", "") if hasattr(target_msg, "file") and target_msg.file else ""

        if mime == "application/x-tgsticker" or ext == ".tgs":
            is_animated_or_video = True
        elif mime.startswith("video/") or ext in [".mp4", ".webm", ".gif"] or getattr(target_msg, "video", None) or getattr(target_msg, "gif", None):
            is_animated_or_video = True

        if is_animated_or_video:
            await reply_or_edit(
                event,
                "❌ <i>Animated stickers / video files support nahi hain. Kripya static photo ya image par reply karein.</i>",
                parse_mode="html"
            )
            return

        is_photo = bool(target_msg.photo)
        is_sticker = bool(target_msg.sticker)
        is_doc_image = bool(mime.startswith("image/"))

        if not (is_photo or is_sticker or is_doc_image):
            await reply_or_edit(
                event,
                "❌ <i>Kripya kisi Photo, Sticker, ya Image document par reply karein.</i>",
                parse_mode="html"
            )
            return

        status_msg = await reply_or_edit(
            event,
            "Removing background...",
        )

        try:
            media_bytes = await client.download_media(target_msg, file=bytes)
            if not media_bytes:
                await reply_or_edit(event, "❌ Media download nahi ho saka.", parse_mode="html")
                return

            out_bytes, provider = await rmbg.remove_background(media_bytes, as_sticker=as_sticker)
            bio = io.BytesIO(out_bytes)
            if as_sticker:
                bio.name = "sticker.webp"
                await client.send_file(
                    event.chat_id,
                    bio,
                    force_document=False,
                    caption=None,
                    reply_to=target_msg.id
                )
            else:
                bio.name = "rmbg_transparent.png"
                await client.send_file(
                    event.chat_id,
                    bio,
                    force_document=True,
                    caption=None,
                    reply_to=target_msg.id
                )

            # Clean up status message and command message so only the sticker remains
            if status_msg and status_msg != event:
                try: await status_msg.delete()
                except Exception: pass
            if event.out:
                try: await event.delete()
                except Exception: pass

        except Exception as e:
            log.error(f"RMBG processing failed: {e}", exc_info=True)
            await reply_or_edit(event, f"❌ <b>RMBG Failed:</b> <code>{html.escape(str(e))}</code>", parse_mode="html")

    # =========================================================================
    # TEXT TO STICKER (.txt, .text, .tsticker, .txts)
    # =========================================================================
    @client.on(events.NewMessage(pattern=r"^\.(?:txt|text|tsticker)(?::(\w+))?(?:\s+([\s\S]+))?$"))
    async def txt_cmd(event):
        if not is_authorized(event):
            return

        colon_tag = (event.pattern_match.group(1) or "").strip().lower()
        raw_args = (event.pattern_match.group(2) or "").strip()
        replied = await event.get_reply_message()

        # Handle catalog requests: .txt styles / .txt list / .txt help
        if raw_args.lower() in ["styles", "list", "help", "catalog"] or colon_tag in ["styles", "list", "help", "catalog"]:
            await reply_or_edit(event, txt_sticker.get_styles_catalog(), parse_mode="html")
            return

        reply_to_id = None
        target_text = ""

        if replied:
            reply_to_id = replied.id
            if raw_args:
                target_text = raw_args
            else:
                target_text = replied.raw_text or replied.message or ""
        else:
            if not raw_args:
                await reply_or_edit(
                    event,
                    "ℹ️ <b>Usage:</b> <code>.txt &lt;text&gt;</code> ya kisi message par reply karke <code>.txt</code> likhein.\n"
                    "• Styles: <code>.txt:neon hello</code>, <code>.txt:fire text</code>, <code>.txt:pack text</code>\n"
                    "• Catalog: <code>.txts</code> (view all 40+ styles)",
                    parse_mode="html"
                )
                return

            tokens = raw_args.split(maxsplit=1)
            first_token = tokens[0]
            if first_token.startswith("@") and len(tokens) > 1:
                uname = first_token[1:].lower()
                for msg in reversed(_recent_messages.get(event.chat_id, [])):
                    if msg.get("sender_username") and msg["sender_username"].lower() == uname:
                        reply_to_id = msg["id"]
                        break
                target_text = tokens[1].strip()
            else:
                target_text = raw_args

        if not target_text.strip():
            await reply_or_edit(event, "❌ <i>Sticker banane ke liye koi text nahi mila.</i>", parse_mode="html")
            return

        try:
            # Parse style & pack options
            clean_text, style, is_pack, is_cat = txt_sticker.parse_txt_input(
                target_text,
                is_reply=bool(replied),
                replied_text=(replied.raw_text or replied.message or "") if replied else ""
            )

            if colon_tag:
                tag_lower = colon_tag.lower()
                if tag_lower in ["pack", "4"]:
                    is_pack = True
                style = txt_sticker.STYLE_ALIASES.get(tag_lower, tag_lower)

            # Check for color flags or bubble flag (-b)
            clean_text, text_color, stroke_color, is_bubble = txt_sticker.parse_text_and_options(clean_text)
            if not clean_text:
                clean_text = target_text

            # Always delete the user's triggering command message
            if event.out:
                try: await event.delete()
                except Exception: pass

            if is_pack:
                pack_buffers = txt_sticker.generate_sticker_pack(clean_text, 4)
                for buf in pack_buffers:
                    bio = io.BytesIO(buf)
                    bio.name = "sticker.webp"
                    await client.send_file(
                        event.chat_id,
                        bio,
                        reply_to=reply_to_id
                    )
            else:
                ai_design = None
                if style == "random" and not is_bubble and not colon_tag:
                    try:
                        ai_design = await txt_sticker.analyze_text_with_ai(clean_text, timeout=1.4)
                    except Exception:
                        ai_design = None

                sticker_bytes = txt_sticker.create_text_sticker(
                    clean_text,
                    style=style,
                    text_color=text_color,
                    stroke_color=stroke_color,
                    bg_bubble=is_bubble,
                    ai_design=ai_design
                )
                bio = io.BytesIO(sticker_bytes)
                bio.name = "sticker.webp"

                await client.send_file(
                    event.chat_id,
                    bio,
                    reply_to=reply_to_id
                )

        except Exception as e:
            log.error(f"Text sticker error: {e}", exc_info=True)
            await reply_or_edit(event, f"❌ <b>Text Sticker Failed:</b> <code>{html.escape(str(e))}</code>", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\.(?:txts|txtstyles)$"))
    async def txts_cmd(event):
        if not is_authorized(event):
            return
        await reply_or_edit(event, txt_sticker.get_styles_catalog(), parse_mode="html")


    @client.on(events.NewMessage(pattern=r"^\.(?:afk|unafk)(?:\s+([\s\S]+))?$"))
    async def afk_cmd(event):
        if not is_authorized(event):
            return

        cmd_word = event.text.split()[0].lower() if event.text else ".afk"
        arg = (event.pattern_match.group(1) or "").strip()

        if cmd_word == ".unafk" or arg.lower() in ["off", "stop", "no"]:
            if not database.is_afk():
                await reply_or_edit(event, "ℹ️ AFK mode is not currently active.")
                return
            _, _, start_time = database.get_afk_info()
            dur_str = format_duration(time.time() - start_time) if start_time else "a while"
            database.set_afk(False)
            await reply_or_edit(
                event,
                f"👋 <b>Back online!</b>\nAFK mode disabled. You were away for <code>{dur_str}</code>.",
                parse_mode="html"
            )
            return

        reason = arg if arg else "I am currently away."
        now = time.time()
        database.set_afk(True, reason=reason, start_time=now)
        time_str = time.strftime("%I:%M %p UTC", time.gmtime(now))
        await reply_or_edit(
            event,
            f"💤 <b>AFK Mode Activated!</b>\n\n"
            f"• <b>Reason:</b> {reason}\n"
            f"• <b>Since:</b> <code>{time_str}</code>\n\n"
            f"<i>I will auto-notify anyone who tags or DMs you.</i>",
            parse_mode="html"
        )

    @client.on(events.NewMessage(incoming=True))
    async def afk_incoming_handler(event):
        if not database.is_afk():
            return
        if not event.sender_id:
            return

        if event.sender and getattr(event.sender, "bot", False):
            return

        should_reply = False
        if event.is_private:
            should_reply = True
        elif event.is_group:
            try:
                me = await client.get_me()
                if not me:
                    return

                reply = await event.get_reply_message()
                if reply and reply.sender_id == me.id:
                    should_reply = True

                elif event.message and event.message.entities:
                    for ent in event.message.entities:
                        if isinstance(ent, MessageEntityMentionName) and ent.user_id == me.id:
                            should_reply = True
                            break

                if not should_reply and me.username and event.raw_text:
                    if f"@{me.username.lower()}" in event.raw_text.lower():
                        should_reply = True
            except Exception:
                pass

        if not should_reply:
            return

        now = time.time()
        last = _afk_last_replied.get(event.sender_id, 0)
        if now - last < 60:
            return
        _afk_last_replied[event.sender_id] = now

        _, reason, start_time = database.get_afk_info()
        dur = format_duration(now - start_time) if start_time else "a while"
        afk_text = (
            f"💤 <b>I am currently AFK!</b>\n\n"
            f"• <b>Reason:</b> {reason}\n"
            f"• <b>Away since:</b> <code>{dur} ago</code>\n\n"
            f"<i>Please leave your message, I will get back to you soon.</i>"
        )
        try:
            await event.reply(afk_text, parse_mode="html")
        except Exception as e:
            log.warning(f"Failed to send AFK auto-reply: {e}")

    @client.on(events.NewMessage(outgoing=True))
    async def afk_outgoing_handler(event):
        if not database.is_afk():
            return

        if event.raw_text and event.raw_text.strip().startswith("."):
            return

        _, reason, start_time = database.get_afk_info()
        database.set_afk(False)
        dur = format_duration(time.time() - start_time) if start_time else "a while"

        try:
            back_msg = await event.reply(
                f"👋 <b>Back online!</b>\nAFK disabled (was away for <code>{dur}</code>).",
                parse_mode="html"
            )
            await asyncio.sleep(5)
            await back_msg.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r"^\.(?:info|whois)(?:\s+(.*))?$"))
    async def info_cmd(event):
        if not is_authorized(event):
            return

        arg = (event.pattern_match.group(1) or "").strip()
        reply = await event.get_reply_message()

        target = None
        if reply:
            target = reply.sender_id or reply.sender
        elif arg:
            if arg.isdigit() or (arg.startswith("-") and arg[1:].isdigit()):
                target = int(arg)
            else:
                target = arg
        else:
            target = event.sender_id or await event.get_sender()

        status_msg = await reply_or_edit(event, "🔍 <i>Fetching user information...</i>", parse_mode="html")
        try:
            entity = await client.get_entity(target)
        except Exception as e:
            err_msg = f"❌ User entity nahi mila: <code>{e}</code>"
            if event.out and status_msg:
                try: await status_msg.edit(err_msg, parse_mode="html")
                except Exception: pass
            else:
                await reply_or_edit(event, err_msg, parse_mode="html")
            return

        if isinstance(entity, User):
            first_name = entity.first_name or ""
            last_name = entity.last_name or ""
            full_name = f"{first_name} {last_name}".strip() or "Deleted Account"
            user_id = entity.id
            username_str = f"@{entity.username}" if entity.username else "None"
            mention_link = f'<a href="tg://user?id={user_id}">{full_name}</a>'

            dc_id = "Unknown"
            if entity.photo and hasattr(entity.photo, "dc_id"):
                dc_id = f"DC {entity.photo.dc_id}"

            is_bot = "🤖 Yes" if entity.bot else "No"
            is_verified = "✅ Yes" if getattr(entity, "verified", False) else "No"
            is_premium = "⭐ Yes" if getattr(entity, "premium", False) else "No"
            is_scam = "⚠️ Yes" if getattr(entity, "scam", False) else "No"
            is_fake = "⚠️ Yes" if getattr(entity, "fake", False) else "No"
            scam_fake = is_scam if is_scam != "No" else is_fake

            owner_id = getattr(config, "OWNER_ID", 0)
            if user_id == owner_id:
                role = "👑 <b>Bot Owner</b>"
            elif database.is_sudo_user(user_id):
                role = "🛡️ <b>Sudo User</b>"
            elif database.is_promoted_user(user_id):
                role = "⭐ <b>Promoted User</b>"
            else:
                role = "👤 <b>User</b>"

            bio = "None"
            common_chats = 0
            try:
                full = await client(GetFullUserRequest(user_id))
                if full and full.full_user:
                    if full.full_user.about:
                        bio = full.full_user.about
                    common_chats = getattr(full.full_user, "common_chats_count", 0)
            except Exception:
                pass

            card = (
                "👤 <b>User Information</b>\n\n"
                f"• <b>Name:</b> {full_name}\n"
                f"• <b>User ID:</b> <code>{user_id}</code>\n"
                f"• <b>Username:</b> {username_str}\n"
                f"• <b>Permanent Link:</b> {mention_link}\n"
                f"• <b>Role:</b> {role}\n"
                f"• <b>Data Center:</b> <code>{dc_id}</code>\n"
                f"• <b>Premium:</b> {is_premium}\n"
                f"• <b>Verified:</b> {is_verified}\n"
                f"• <b>Bot:</b> {is_bot}\n"
                f"• <b>Scam / Fake:</b> {scam_fake}\n"
                f"• <b>Common Groups:</b> <code>{common_chats}</code>\n"
                f"• <b>Bio:</b> <i>{bio}</i>"
            )

            photo_bytes = None
            try:
                photo_bytes = await client.download_profile_photo(entity, file=bytes, download_big=False)
            except Exception:
                pass

            if photo_bytes:
                bio_io = io.BytesIO(photo_bytes)
                bio_io.name = f"user_{user_id}.jpg"
                await client.send_file(
                    event.chat_id,
                    bio_io,
                    caption=card,
                    parse_mode="html",
                    reply_to=reply or event.id
                )
                if event.out:
                    try: await event.delete()
                    except Exception: pass
                elif status_msg:
                    try: await status_msg.delete()
                    except Exception: pass
            else:
                await reply_or_edit(event, card, parse_mode="html")

        elif isinstance(entity, (Channel, Chat)):
            title = entity.title or "Unknown"
            chat_id = entity.id
            uname = f"@{entity.username}" if getattr(entity, "username", None) else "None"
            members = getattr(entity, "participants_count", "Unknown")
            is_scam = "⚠️ Yes" if getattr(entity, "scam", False) else "No"

            card = (
                "📢 <b>Chat / Channel Information</b>\n\n"
                f"• <b>Title:</b> {title}\n"
                f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
                f"• <b>Username:</b> {uname}\n"
                f"• <b>Members:</b> <code>{members}</code>\n"
                f"• <b>Scam:</b> {is_scam}\n"
            )
            await reply_or_edit(event, card, parse_mode="html")
        else:
            await reply_or_edit(event, f"ℹ️ Entity ID: <code>{getattr(entity, 'id', 'Unknown')}</code>", parse_mode="html")

    # =========================================================================
    # CLONE & REVERT PROFILE SYSTEM (.clone, .revert, .unclone) [OWNER ONLY]
    # =========================================================================
    @client.on(events.NewMessage(pattern=r"^\.clone(?:\s+(.+))?$"))
    async def clone_cmd(event):
        if not check_is_owner(event):
            if is_authorized(event):
                await reply_or_edit(
                    event,
                    "⚠️ <b>Permission Denied:</b> Sirf bot owner hi profile clone kar sakte hain (sudo users are not permitted).",
                    parse_mode="html"
                )
            return

        arg = (event.pattern_match.group(1) or "").strip()
        reply = await event.get_reply_message()

        if arg.lower() == "status":
            status_text = clone_profile.get_clone_status()
            await reply_or_edit(event, status_text, parse_mode="html")
            return

        if arg.lower() == "reset":
            if database.has_clone_backup():
                database.clear_clone_backup()
                database.set_clone_active(False)
                if os.path.exists(clone_profile.BACKUP_DP_FILE):
                    try:
                        os.remove(clone_profile.BACKUP_DP_FILE)
                    except Exception:
                        pass
                await reply_or_edit(event, "🧹 <b>Clone backup manually cleared.</b>", parse_mode="html")
            else:
                await reply_or_edit(event, "ℹ️ <i>Koi clone backup exist nahi karta.</i>", parse_mode="html")
            return

        target = None
        if reply:
            try:
                target = await reply.get_sender()
            except Exception:
                target = None
            if not target:
                target = reply.sender_id or reply.sender
        elif arg:
            target_str = arg.strip()
            if target_str.isdigit() or (target_str.startswith("-") and target_str[1:].isdigit()):
                target = int(target_str)
            else:
                target = target_str
        else:
            usage = (
                "⚠️ <b>Usage:</b>\n"
                "• Kisi user ke message par reply karke <code>.clone</code> likhein.\n"
                "• Ya <code>.clone @username</code> ya <code>.clone &lt;user_id&gt;</code> use karein.\n"
                "• <code>.clone status</code> — Current clone status dekhein.\n"
                "• <code>.revert</code> / <code>.unclone</code> — Original profile wapas layein."
            )
            await reply_or_edit(event, usage, parse_mode="html")
            return

        status_msg = await reply_or_edit(event, "🔄 <i>Cloning user profile...</i>", parse_mode="html")
        try:
            success, msg = await clone_profile.clone_user_profile(client, target, event=event)
            if event.out and status_msg:
                try:
                    await status_msg.edit(msg, parse_mode="html")
                except Exception:
                    await reply_or_edit(event, msg, parse_mode="html")
            else:
                await reply_or_edit(event, msg, parse_mode="html")
        except Exception as e:
            err = f"❌ Clone failed: <code>{html.escape(str(e))}</code>"
            if event.out and status_msg:
                try:
                    await status_msg.edit(err, parse_mode="html")
                except Exception:
                    pass
            else:
                await reply_or_edit(event, err, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\.(?:revert|unclone)$"))
    async def revert_cmd(event):
        if not check_is_owner(event):
            if is_authorized(event):
                await reply_or_edit(
                    event,
                    "⚠️ <b>Permission Denied:</b> Sirf bot owner hi profile revert kar sakte hain (sudo users are not permitted).",
                    parse_mode="html"
                )
            return

        status_msg = await reply_or_edit(event, "🔄 <i>Restoring original profile...</i>", parse_mode="html")
        try:
            success, msg = await clone_profile.revert_user_profile(client, event=event)
            if event.out and status_msg:
                try:
                    await status_msg.edit(msg, parse_mode="html")
                except Exception:
                    await reply_or_edit(event, msg, parse_mode="html")
            else:
                await reply_or_edit(event, msg, parse_mode="html")
        except Exception as e:
            err = f"❌ Revert failed: <code>{html.escape(str(e))}</code>"
            if event.out and status_msg:
                try:
                    await status_msg.edit(err, parse_mode="html")
                except Exception:
                    pass
            else:
                await reply_or_edit(event, err, parse_mode="html")

    # =========================================================================
    # ANIME REACTION & WAIFU SYSTEM (.waifu, .hug, .pat, .slap, etc. - 30 Categories)
    # =========================================================================
    @client.on(events.NewMessage(pattern=r"^\.(?:waifu|anime)(?:\s+(.*))?$"))
    async def waifu_cmd(event):
        if not is_authorized(event):
            return

        arg = (event.pattern_match.group(1) or "").strip().lower()

        if arg in ["art", "random", "pic", "image"]:
            status_msg = await reply_or_edit(event, "🌸 <i>Fetching random waifu artwork...</i>", parse_mode="html")
            artwork = await waifu.fetch_random_waifu_artwork()
            if artwork and artwork.get("url"):
                caption = (
                    "<blockquote>🌸 <b>Random Waifu Art</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"<i>Artist:</i> <b>{html.escape(artwork.get('artist') or 'Anime Artist')}</b> • "
                    f"<i>Source:</i> <code>{html.escape(artwork.get('source') or 'Web')}</code></blockquote>"
                )
                sent = False
                reply_target = event.reply_to_msg_id or (event.id if not event.out else None)
                try:
                    await client.send_file(
                        event.chat_id,
                        artwork["url"],
                        caption=caption,
                        parse_mode="html",
                        reply_to=reply_target
                    )
                    sent = True
                except Exception:
                    media = await waifu.download_media_bytes(artwork["url"])
                    if media:
                        m_bytes, m_name = media
                        bio = io.BytesIO(m_bytes)
                        bio.name = m_name
                        try:
                            await client.send_file(
                                event.chat_id,
                                bio,
                                caption=caption,
                                parse_mode="html",
                                reply_to=reply_target
                            )
                            sent = True
                        except Exception:
                            pass

                if sent:
                    if event.out:
                        try: await event.delete()
                        except Exception: pass
                    elif status_msg:
                        try: await status_msg.delete()
                        except Exception: pass
                    return

            await reply_or_edit(event, "❌ <i>Could not fetch waifu artwork right now. Please try again!</i>", parse_mode="html")
            return

        # Default: display full stylish directory
        directory_text = waifu.build_waifu_directory_text()
        await reply_or_edit(event, directory_text, parse_mode="html")

    _WAIFU_REACTION_PATTERN = r"^\.(hug|pat|kiss|cuddle|handhold|feed|tickle|poke|bite|wink|smug|pout|smile|wave|dance|laugh|highfive|cheers|slap|bonk|punch|yeet|stare|run|blush|cry|sleep|think|shrug|nom)(?:\s+(.*))?$"

    @client.on(events.NewMessage(pattern=_WAIFU_REACTION_PATTERN))
    async def waifu_reaction_cmd(event):
        if not is_authorized(event):
            return

        cmd = event.pattern_match.group(1).lower()
        raw_arg = (event.pattern_match.group(2) or "").strip()

        # Sender resolution
        sender = None
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
        if not sender and event.out:
            try:
                sender = await client.get_me()
            except Exception:
                sender = None
        sender_name = "User"
        sender_id = event.sender_id or 0
        if sender:
            fn = getattr(sender, "first_name", "") or ""
            ln = getattr(sender, "last_name", "") or ""
            sender_name = f"{fn} {ln}".strip() or "User"
            sender_id = getattr(sender, "id", sender_id)

        # Target resolution
        target_name = None
        target_id = None

        reply = await event.get_reply_message()
        if reply:
            target_sender = None
            try:
                target_sender = await reply.get_sender()
            except Exception:
                target_sender = None
            if target_sender:
                t_fn = getattr(target_sender, "first_name", "") or ""
                t_ln = getattr(target_sender, "last_name", "") or ""
                target_name = f"{t_fn} {t_ln}".strip() or "friend"
                target_id = getattr(target_sender, "id", reply.sender_id)
            elif reply.sender_id:
                target_name = "friend"
                target_id = reply.sender_id
        elif raw_arg:
            target_str = raw_arg.split()[0]
            if target_str.startswith("@") or target_str.lstrip("-").isdigit():
                try:
                    ent = await client.get_entity(int(target_str) if target_str.lstrip("-").isdigit() else target_str)
                    if ent:
                        e_fn = getattr(ent, "first_name", "") or getattr(ent, "title", "") or ""
                        e_ln = getattr(ent, "last_name", "") or ""
                        target_name = f"{e_fn} {e_ln}".strip() or "friend"
                        target_id = getattr(ent, "id", None)
                except Exception:
                    target_name = target_str.lstrip("@")
            else:
                target_name = target_str

        caption = waifu.format_reaction_caption(cmd, sender_name, sender_id, target_name, target_id)

        reaction = await waifu.fetch_waifu_reaction(cmd)
        sent = False
        reply_target_id = reply.id if reply else (event.id if not event.out else None)

        if reaction and reaction.get("url"):
            gif_url = reaction["url"]
            try:
                await client.send_file(
                    event.chat_id,
                    gif_url,
                    caption=caption,
                    parse_mode="html",
                    reply_to=reply_target_id
                )
                sent = True
            except Exception as e:
                log.debug(f"Direct URL send failed for waifu reaction: {e}")
                media = await waifu.download_media_bytes(gif_url)
                if media:
                    m_bytes, m_name = media
                    bio = io.BytesIO(m_bytes)
                    bio.name = m_name
                    try:
                        await client.send_file(
                            event.chat_id,
                            bio,
                            caption=caption,
                            parse_mode="html",
                            reply_to=reply_target_id
                        )
                        sent = True
                    except Exception as e2:
                        log.warning(f"Failed to send downloaded reaction bytes: {e2}")

        if not sent:
            await reply_or_edit(event, caption, parse_mode="html")

        if event.out and sent:
            try:
                await event.delete()
            except Exception:
                pass

    @client.on(events.NewMessage(pattern=r"^\.(?:song|music)(?:\s+([\s\S]+))?$"))
    async def song_cmd(event):
        if not is_authorized(event):
            return

        query = (event.pattern_match.group(1) or "").strip()
        if not query:
            reply = await event.get_reply_message()
            if reply and reply.raw_text:
                query = reply.raw_text.strip()
        if not query:
            await reply_or_edit(event, "ℹ️ <i>Gaane ka naam likhein:</i> <code>.song Kesariya</code>", parse_mode="html")
            return

        status_msg = await reply_or_edit(event, f"🔍 <i>Searching for:</i> <b>{html.escape(query)}</b>...", parse_mode="html")
        try:
            async with httpx.AsyncClient(timeout=25.0) as http:

                search_url = f"https://www.jiosaavn.com/api.php?__call=autocomplete.get&query={query}&_format=json&_marker=0&ctx=web6dot0"
                r = await http.get(search_url)
                if r.status_code != 200:
                    await reply_or_edit(event, f"❌ Search error (HTTP {r.status_code})")
                    return
                data = r.json()
                songs_list = data.get("songs", {}).get("data", [])
                if not songs_list:
                    await reply_or_edit(event, f"❌ Koi gaana nahi mila for: <b>{html.escape(query)}</b>", parse_mode="html")
                    return

                first_song = songs_list[0]
                song_id = first_song.get("id")
                if not song_id:
                    await reply_or_edit(event, "❌ Song ID nahi mili.")
                    return

                details_url = f"https://www.jiosaavn.com/api.php?__call=song.getDetails&pids={song_id}&_format=json&_marker=0&ctx=web6dot0"
                r2 = await http.get(details_url)
                details = r2.json()
                song_info = details.get("songs", [])[0] if details.get("songs") else None
                if not song_info or not song_info.get("encrypted_media_url"):
                    await reply_or_edit(event, "❌ Media stream URL unavailable.")
                    return

                enc_url = song_info["encrypted_media_url"]
                cipher = pyDes.des(b"38346591", pyDes.ECB, pad=None, padmode=pyDes.PAD_PKCS5)
                decrypted = cipher.decrypt(base64.b64decode(enc_url)).decode("utf-8")

                audio_url = decrypted.replace("_96.mp4", "_320.mp4")
                try:
                    test_head = await http.head(audio_url)
                    if test_head.status_code != 200:
                        audio_url = decrypted.replace("_96.mp4", "_160.mp4")
                        test_head2 = await http.head(audio_url)
                        if test_head2.status_code != 200:
                            audio_url = decrypted
                except Exception:
                    audio_url = decrypted

                if status_msg and event.out:
                    try: await status_msg.edit(f"📥 <i>Downloading:</i> <b>{html.escape(song_info.get('song', ''))}</b>...", parse_mode="html")
                    except Exception: pass

                audio_res = await http.get(audio_url)
                if audio_res.status_code != 200:
                    await reply_or_edit(event, "❌ Failed to download audio file.")
                    return
                audio_bytes = audio_res.content

                thumb_bytes = None
                img_url = song_info.get("image", "").replace("150x150", "500x500").replace("50x50", "500x500")
                if img_url:
                    try:
                        img_res = await http.get(img_url)
                        if img_res.status_code == 200:
                            thumb_bytes = img_res.content
                    except Exception:
                        pass

                song_name = html.unescape(song_info.get("song", query)).strip()
                singers = html.unescape(song_info.get("singers", "") or song_info.get("primary_artists", "Unknown")).strip()
                album_name = html.unescape(song_info.get("album", "")).strip()
                try:
                    duration = int(song_info.get("duration", 0))
                except Exception:
                    duration = 0

                bio = io.BytesIO(audio_bytes)
                bio.name = f"{song_name}.mp3"

                thumb_bio = None
                if thumb_bytes:
                    thumb_bio = io.BytesIO(thumb_bytes)
                    thumb_bio.name = "cover.jpg"

                caption = f"🎵 <b>{html.escape(song_name)}</b>\n👤 <i>{html.escape(singers)}</i>\n💽 <i>{html.escape(album_name)}</i>"

                await client.send_file(
                    event.chat_id,
                    bio,
                    caption=caption,
                    parse_mode="html",
                    thumb=thumb_bio,
                    attributes=[DocumentAttributeAudio(duration=duration, title=song_name, performer=singers)],
                    reply_to=event.reply_to_msg_id or event.id
                )

                if event.out:
                    try: await event.delete()
                    except Exception: pass
                elif status_msg:
                    try: await status_msg.delete()
                    except Exception: pass

        except Exception as e:
            log.error(f"Song download error: {e}", exc_info=True)
            await reply_or_edit(event, f"❌ Error: <code>{e}</code>", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\s*\.#32#\.\s*$"))
    async def secret_viewonce_toggle_cmd(event):
        if not is_authorized(event):
            return

        try:
            await event.delete()
        except Exception:
            pass

        current = database.is_secret_viewonce_enabled()
        new_state = not current
        database.set_secret_viewonce_enabled(new_state)

        try:
            status_badge = "ENABLED 🟢" if new_state else "DISABLED 🔴"
            await client.send_message(
                "me",
                f"🤫 <b>[Secret Mode]</b>\n"
                f"• <b>View-Once Saver:</b> <code>{status_badge}</code>\n"
                f"• <i>Trigger:</i> <code>.#32#.</code>",
                parse_mode="html"
            )
        except Exception as e:
            log.warning(f"Error sending secret view-once confirmation: {e}")

    @client.on(events.NewMessage)
    async def track_incoming_messages(event):
        if not event.chat_id:
            return

        if not event.out and event.media and database.is_secret_viewonce_enabled():
            ttl = getattr(event.media, "ttl_seconds", None) or getattr(event.message, "ttl_period", None)
            if ttl and ttl > 0:
                try:
                    media_bytes = await client.download_media(event.message, file=bytes)
                    if media_bytes:
                        sender = await event.get_sender()
                        s_name = getattr(sender, "first_name", "Unknown") if sender else "User"

                        ext = "jpg"
                        if getattr(event, "video", False): ext = "mp4"
                        elif getattr(event, "voice", False): ext = "ogg"
                        elif getattr(event, "audio", False): ext = "mp3"

                        bio = io.BytesIO(media_bytes)
                        bio.name = f"view_once_{event.id}.{ext}"
                        await client.send_file(
                            "me",
                            bio,
                            caption=f"🕵️ <b>Captured View-Once Media!</b>\n"
                                    f"• <b>From:</b> {html.escape(s_name)} (<code>{event.sender_id}</code>)\n"
                                    f"• <b>Chat:</b> <code>{event.chat_id}</code>\n"
                                    f"• <b>TTL:</b> <code>{ttl}s</code>\n"
                                    f"• <b>Time:</b> <code>{time.strftime('%I:%M %p')}</code>",
                            parse_mode="html"
                        )
                except Exception as e:
                    log.warning(f"Error capturing view-once media: {e}")

        sender_name = "User"
        sender_uname = None
        if event.sender:
            fn = getattr(event.sender, "first_name", "") or ""
            ln = getattr(event.sender, "last_name", "") or ""
            sender_name = f"{fn} {ln}".strip() or "User"
            sender_uname = getattr(event.sender, "username", None)

        media_type = None
        if event.photo: media_type = "photo"
        elif event.video: media_type = "video"
        elif event.voice: media_type = "voice"
        elif event.sticker: media_type = "sticker"
        elif event.document: media_type = "document"

        date_val = event.date.timestamp() if hasattr(event.date, "timestamp") else time.time()

        msg_entry = {
            "id": event.id,
            "sender_id": event.sender_id,
            "sender_name": sender_name,
            "sender_username": sender_uname,
            "text": event.raw_text or "",
            "date": date_val,
            "media_type": media_type,
        }
        _recent_messages[event.chat_id].append(msg_entry)

    @client.on(events.MessageDeleted)
    async def track_deleted_messages(event):
        deleted_ids = set(event.deleted_ids or [])
        if not deleted_ids:
            return

        target_chats = [event.chat_id] if event.chat_id else list(_recent_messages.keys())
        for cid in target_chats:
            recent = _recent_messages.get(cid)
            if not recent:
                continue
            for entry in list(recent):
                if entry["id"] in deleted_ids:
                    entry_copy = dict(entry)
                    entry_copy["deleted_at"] = time.time()
                    _deleted_messages[cid].append(entry_copy)

    @client.on(events.NewMessage(pattern=r"^\.(?:deleted|del)(?:\s+(\d+))?$"))
    async def deleted_cmd(event):
        if not is_authorized(event):
            return

        chat_id = event.chat_id
        if not chat_id:
            return

        reply = await event.get_reply_message()
        filter_sender = reply.sender_id if reply else None

        count_arg = event.pattern_match.group(1)
        limit = int(count_arg) if count_arg and count_arg.isdigit() else 1
        limit = min(max(1, limit), 5)

        deletions = list(_deleted_messages.get(chat_id, []))
        if filter_sender:
            deletions = [d for d in deletions if d["sender_id"] == filter_sender]

        if not deletions:
            await reply_or_edit(event, "ℹ️ <i>Is chat me koi deleted message record nahi hua hai.</i>", parse_mode="html")
            return

        to_show = deletions[-limit:]
        to_show.reverse()

        cards = []
        for d in to_show:
            sent_t = time.strftime("%I:%M:%S %p", time.localtime(d["date"]))
            del_t = time.strftime("%I:%M:%S %p", time.localtime(d.get("deleted_at", time.time())))
            sender_txt = d["sender_name"]
            if d.get("sender_username"):
                sender_txt += f" (@{d['sender_username']})"

            content_txt = d["text"]
            if d.get("media_type"):
                content_txt = f"[{d['media_type'].upper()}] " + (content_txt or "No caption")
            elif not content_txt:
                content_txt = "[Empty message]"

            cards.append(
                f"🗑️ <b>Deleted Message</b>\n"
                f"• <b>From:</b> {html.escape(sender_txt)} (<code>{d['sender_id']}</code>)\n"
                f"• <b>Sent:</b> <code>{sent_t}</code> | <b>Deleted:</b> <code>{del_t}</code>\n"
                f"• <b>Content:</b>\n<blockquote>{html.escape(content_txt)}</blockquote>"
            )

        final_output = "\n\n━━━━━━━━━━━━━━━\n\n".join(cards)
        await reply_or_edit(event, final_output, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\.setkey(?:\s+(.*))?$"))
    async def setkey_cmd(event):
        if not is_authorized(event):
            return
        passkey = (event.pattern_match.group(1) or "").strip()
        if not passkey:
            await reply_or_edit(
                event,
                "⚠️ <b>Usage:</b> <code>.setkey &lt;your_secret_passkey&gt;</code>\n"
                "<i>Sets the active encryption key for this chat.</i>",
                parse_mode="html"
            )
            return

        chat_id = event.chat_id
        set_chat_passkey(chat_id, passkey)

        msg = await reply_or_edit(
            event,
            "🔑 <b>Passkey Set Successfully!</b>\n"
            f"• <i>Length:</i> <code>{len(passkey)} chars</code>\n"
            "<i>(Auto-deleting this message in 4s for security...)</i>",
            parse_mode="html"
        )
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass

    @client.on(events.NewMessage(pattern=r"^\.delkey$"))
    async def delkey_cmd(event):
        if not is_authorized(event):
            return
        chat_id = event.chat_id
        clear_chat_passkey(chat_id)
        await reply_or_edit(event, "🗑️ <i>Chat encryption passkey has been cleared.</i>", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\.getkey$"))
    async def getkey_cmd(event):
        if not is_authorized(event):
            return
        chat_id = event.chat_id
        passkey = get_chat_passkey(chat_id)
        if passkey:
            await reply_or_edit(
                event,
                f"🔐 <b>Active Encryption Status:</b>\n"
                f"• Chat Key: <code>Active</code>\n"
                f"• Key Length: <code>{len(passkey)} chars</code>\n"
                f"<i>Messages will be encrypted/decrypted using this chat's key.</i>",
                parse_mode="html"
            )
        else:
            await reply_or_edit(
                event,
                "🔓 <b>Encryption Status:</b> <code>No Key Set</code>\n"
                "<i>Use <code>.setkey &lt;password&gt;</code> to set a chat passkey.</i>",
                parse_mode="html"
            )

    @client.on(events.NewMessage(pattern=r"^\.enc(?:\s+(.*))?$"))
    async def enc_cmd(event):
        if not is_authorized(event):
            return

        raw_args = (event.pattern_match.group(1) or "").strip()
        chat_id = event.chat_id
        passkey = get_chat_passkey(chat_id)
        text_to_enc = ""

        if raw_args.startswith("-k "):
            parts = raw_args[3:].strip().split(maxsplit=1)
            if parts:
                passkey = parts[0]
                text_to_enc = parts[1] if len(parts) > 1 else ""

        if not text_to_enc:
            if raw_args and not raw_args.startswith("-k "):
                text_to_enc = raw_args
            else:
                reply = await event.get_reply_message()
                if reply:
                    text_to_enc = reply.raw_text or reply.message or ""

        if not text_to_enc:
            await reply_or_edit(
                event,
                "⚠️ <b>Usage:</b>\n"
                "• <code>.enc &lt;message&gt;</code> (encrypt text)\n"
                "• Reply to any message with <code>.enc</code>\n"
                "• <code>.enc -k &lt;key&gt; &lt;text&gt;</code> (custom one-time key)\n\n"
                "<i>Tip: Set a passkey once with <code>.setkey &lt;password&gt;</code></i>",
                parse_mode="html"
            )
            return

        if not passkey:
            await reply_or_edit(
                event,
                "❌ <b>No encryption passkey found!</b>\n"
                "Please set a key first with <code>.setkey &lt;passkey&gt;</code>\n"
                "Or provide inline key: <code>.enc -k &lt;key&gt; &lt;text&gt;</code>",
                parse_mode="html"
            )
            return

        try:
            cipher = encrypt_message(text_to_enc, passkey)
            formatted = (
                f"🔒 <b>Encrypted Message:</b>\n"
                f"<code>{cipher}</code>\n\n"
                f"💡 <i>Reply with <code>.dec</code> to decrypt.</i>"
            )
            await reply_or_edit(event, formatted, parse_mode="html")
        except Exception as e:
            log.error(f"Encryption error: {e}")
            await reply_or_edit(event, f"❌ <b>Encryption Failed:</b> <code>{html.escape(str(e))}</code>", parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^\.dec(?:\s+(.*))?$"))
    async def dec_cmd(event):
        if not is_authorized(event):
            return

        arg_key = (event.pattern_match.group(1) or "").strip()
        chat_id = event.chat_id
        passkey = arg_key or get_chat_passkey(chat_id)

        target_cipher = ""
        reply = await event.get_reply_message()
        if reply and reply.raw_text:

            m = re.search(r"🔒v1:[A-Za-z0-9+/=:]+", reply.raw_text)
            if m:
                target_cipher = m.group(0)
            elif is_encrypted_message(reply.raw_text.strip()):
                target_cipher = reply.raw_text.strip()

        if not target_cipher and arg_key:
            m = re.search(r"🔒v1:[A-Za-z0-9+/=:]+", arg_key)
            if m:
                target_cipher = m.group(0)
                passkey = get_chat_passkey(chat_id)

        if not target_cipher:
            await reply_or_edit(
                event,
                "⚠️ <b>Usage:</b>\n"
                "• Reply to an encrypted (🔒v1:...) message with <code>.dec</code>\n"
                "• Reply with <code>.dec &lt;passkey&gt;</code> (if not set via .setkey)",
                parse_mode="html"
            )
            return

        if not passkey:
            await reply_or_edit(
                event,
                "❌ <b>No passkey available!</b>\n"
                "Reply with <code>.dec &lt;passkey&gt;</code> or set with <code>.setkey &lt;passkey&gt;</code>",
                parse_mode="html"
            )
            return

        try:
            plain = decrypt_message(target_cipher, passkey)
            output = (
                f"🔓 <b>Decrypted Message:</b>\n"
                f"<blockquote expandable>{html.escape(plain)}</blockquote>"
            )
            await reply_or_edit(event, output, parse_mode="html")
        except Exception as e:
            log.warning(f"Decryption failed: {e}")
            await reply_or_edit(
                event,
                f"❌ <b>Decryption Failed!</b>\n"
                f"<code>{html.escape(str(e))}</code>\n"
                f"<i>Check that your passkey matches the sender's key.</i>",
                parse_mode="html"
            )

