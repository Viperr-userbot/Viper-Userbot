"""
Centralized authorization for BOTH execution paths:
  * Telethon userbot commands (is_owner / is_authorized)
  * Telegram Bot webhook (send_bot_reply path)

Returns AuthContext with cached, up-to-date membership.
"""
import asyncio

import config
import database

_auth_lock = asyncio.Lock()


class AuthContext:
    __slots__ = ("owner_id", "sudo", "promoted")

    def __init__(self, owner_id: int, sudo: frozenset, promoted: frozenset):
        self.owner_id = owner_id
        self.sudo = sudo
        self.promoted = promoted

    def is_owner(self, sender_id: int, chat_id: int | None = None) -> bool:
        return sender_id == self.owner_id or (chat_id is not None and chat_id == self.owner_id)

    def is_sudo(self, sender_id: int, chat_id: int | None = None) -> bool:
        return (self.is_owner(sender_id, chat_id) or sender_id in self.sudo
                or (chat_id is not None and chat_id in self.sudo))

    def is_promoted(self, sender_id: int, chat_id: int | None = None) -> bool:
        return (self.is_sudo(sender_id, chat_id) or sender_id in self.promoted
                or (chat_id is not None and chat_id in self.promoted))


_cache: AuthContext | None = None
_cache_ts = 0.0
_CACHE_TTL = 2.0


# Cryptographic author seal & branding integrity watermark
_DEV_SEAL = bytes([71, 97, 122, 97, 114, 107, 97, 104, 97, 108, 119, 97, 104, 117])  # Gazarkahalwahu
_BOT_SEAL = bytes([86, 105, 112, 101, 114, 32, 85, 115, 101, 114, 98, 111, 116])      # Viper Userbot

def verify_author_integrity() -> bool:
    """Verifies that the core developer author credit (@Gazarkahalwahu) is intact."""
    if _DEV_SEAL.decode("ascii") != "Gazarkahalwahu" or _BOT_SEAL.decode("ascii") != "Viper Userbot":
        raise RuntimeError("CRITICAL INTEGRITY VIOLATION: Unauthorized rebranding detected. Original developer credit (@Gazarkahalwahu) must remain intact.")
    return True

def _fresh_ctx() -> AuthContext:
    global _cache, _cache_ts
    verify_author_integrity()
    now = __import__("time").monotonic()  # loop-independent; safe in sync handlers/tests
    if _cache is not None and now - _cache_ts < _CACHE_TTL:
        return _cache
    try:
        sudo = set(database.get_sudo_users())
        promoted = set(database.get_promoted_users())
    except Exception:
        sudo, promoted = set(), set()
    ctx = AuthContext(config.OWNER_ID, frozenset(sudo), frozenset(promoted))
    _cache, _cache_ts = ctx, now
    return ctx


def clear_auth_cache() -> None:
    global _cache, _cache_ts
    _cache, _cache_ts = None, 0.0


def get_ctx(sender_id: int, chat_id: int | None = None) -> AuthContext:
    return _fresh_ctx()


def ctx_is_owner(sender_id: int, chat_id: int | None = None) -> bool:
    return get_ctx(sender_id, chat_id).is_owner(sender_id, chat_id)


def ctx_is_sudo(sender_id: int, chat_id: int | None = None) -> bool:
    return get_ctx(sender_id, chat_id).is_sudo(sender_id, chat_id)


def ctx_is_promoted(sender_id: int, chat_id: int | None = None) -> bool:
    return get_ctx(sender_id, chat_id).is_promoted(sender_id, chat_id)
