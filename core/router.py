"""
Request router + HMAC webhook signature verifier.
Shared between Telethon userbot and Telegram Bot webhook path.
"""
import hmac
import logging
import time
from typing import NamedTuple

import config

from core.auth import ctx_is_owner, ctx_is_sudo, ctx_is_promoted

log = logging.getLogger("hwh.router")

_RATE_WINDOW_S = 0.45
_last_seen: dict[int, float] = {}


class RouterContext(NamedTuple):
    """Auth-decided context attached to every webhook/TG message handler."""
    sender_id: int
    chat_id: int
    chat_type: str
    is_owner: bool
    is_sudo: bool
    is_promoted: bool
    text: str
    msg_id: int | None


def make_webhook_token_sign(secret: str) -> bytes:
    return b"WebhookSecret" + secret.encode()


_TOKEN_SIGN: bytes | None = None
if config.WEBHOOK_SIGNATURE_TOKEN:
    _TOKEN_SIGN = make_webhook_token_sign(config.WEBHOOK_SIGNATURE_TOKEN)


def verify_request(body: bytes, headers) -> bool:
    """Verify a webhook request accepts either transport Telegram offers:
      * X-Telegram-Bot-Api-Secret-Token : secret_token passed to setWebhook
      * X-Telegram-Webhook-Signature    : HMAC-SHA256 of the raw body
    Fails closed when WEBHOOK_SIGNATURE_TOKEN is not configured.
    """
    if _TOKEN_SIGN is None:
        log.warning("Webhook HMAC verification DISABLED: set WEBHOOK_SIGNATURE_TOKEN in .env")
        return False  # fail closed when not configured
    secret = config.WEBHOOK_SIGNATURE_TOKEN
    hdr = headers.get("x-telegram-bot-api-secret-token")
    if hdr and hmac.compare_digest(str(hdr), str(secret)):
        return True
    sig = headers.get("x-telegram-webhook-signature")
    return verify_webhook_signature(body, sig)


def compute_webhook_signature(token: str, body: bytes) -> str:
    """HMAC-SHA256 signature (X-Telegram-Webhook-Signature header)."""
    secret = _TOKEN_SIGN if _TOKEN_SIGN is not None else b"WebhookSecret" + token.encode()
    return hmac.new(secret, body, "sha256").hexdigest()


def verify_webhook_signature(body: bytes, supplied: str | None) -> bool:
    """Return True only when the webhook signature matches (or HMAC is disabled)."""
    if _TOKEN_SIGN is None:
        log.warning("Webhook HMAC verification DISABLED: set WEBHOOK_SIGNATURE_TOKEN in .env")
        return False  # fail closed when not configured
    expected = compute_webhook_signature("unused", body)
    return hmac.compare_digest(expected, supplied) if supplied else False


def _debounce(chat_id: int, skip: bool = False) -> bool:
    """Returns True when the request should be dropped as a duplicate burst."""
    now = time.monotonic()
    if skip:
        _last_seen[chat_id] = now
        return False
    if chat_id in _last_seen and now - _last_seen[chat_id] < _RATE_WINDOW_S:
        return True
    _last_seen[chat_id] = now
    return False


debounce = _debounce


def ctx_from_webhook(update: dict) -> RouterContext | None:
    """Build a RouterContext from a Telegram Bot webhook update.
    Returns None when the webhook is unauthenticated / malformed / over-sized.
    """
    if not isinstance(update, dict):
        log.warning("Webhook received non-dict body")
        return None

    if "callback_query" in update:
        cb = update.get("callback_query")
        if not isinstance(cb, dict):
            return None
        from_obj = cb.get("from") if isinstance(cb.get("from"), dict) else {}
        user_id = from_obj.get("id")
        msg_obj = cb.get("message") if isinstance(cb.get("message"), dict) else {}
        chat_obj = msg_obj.get("chat") if isinstance(msg_obj.get("chat"), dict) else {}
        chat_id = chat_obj.get("id") or user_id
        raw_text = str(cb.get("data") or "")
        text = raw_text[:4096]
        msg_id = msg_obj.get("message_id")
        chat_type = chat_obj.get("type", "private")
        is_msg = False
    elif "message" in update:
        msg = update.get("message")
        if not isinstance(msg, dict):
            return None
        raw_text = msg.get("text") or msg.get("caption") or ""
        if not isinstance(raw_text, str) or len(raw_text) > 4096:
            text = ""  # ignore over-length text
        else:
            text = raw_text.strip()
        chat_obj = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
        chat_id = chat_obj.get("id")
        from_obj = msg.get("from") if isinstance(msg.get("from"), dict) else {}
        sender_id = from_obj.get("id") or chat_id
        msg_id = msg.get("message_id")
        chat_type = chat_obj.get("type", "private")
        is_msg = True
    else:
        return None

    if not chat_id or not isinstance(chat_id, int):
        return None

    sid = sender_id if is_msg else user_id
    if not sid or not isinstance(sid, int):
        sid = chat_id

    return RouterContext(
        sender_id=sid,
        chat_id=chat_id,
        chat_type=str(chat_type or "private"),
        is_owner=ctx_is_owner(sid, chat_id),
        is_sudo=ctx_is_sudo(sid, chat_id),
        is_promoted=ctx_is_promoted(sid, chat_id),
        text=text,
        msg_id=msg_id if isinstance(msg_id, int) else None,
    )
