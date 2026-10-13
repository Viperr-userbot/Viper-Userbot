"""
Interactive Category-Based Help Menu for Viper Userbot.
Provides clean inline button navigation and complete command documentation.
"""
from typing import Any

HELP_CATEGORIES: dict[str, dict[str, Any]] = {
    "games": {
        "title": "🎮 Game Solvers & Automation",
        "description": (
            "🎮 <b>Game Solvers & Automation</b>\n\n"
            "• <code>.k</code> / <code>.c</code> — Solve Wordly, Hidden Word Hunt & 10Things\n"
            "• <code>.g [link]</code> / <code>.grab [link]</code> — Grab restricted media from private GC link (e.g. <code>.g https://t.me/c/...</code>) ya reply to bot DM\n"
            "• <code>.gcreply on / off / status</code> — 1-word direct GC reply + point check\n"
            "• <code>.humanize on / off / status</code> — Natural typing delay (1s - 8s)\n"

            "• <i>Smart Gameplay:</i> Verifies +3 points, skips opponent answers, retries new grid words"
        ),
    },
    "ai": {
        "title": "🤖 AI Assistant & Models",
        "description": (
            "🤖 <b>AI Assistant & Models</b>\n\n"
            "• <code>.gpt &lt;query&gt;</code> — Ask AI (Text & Vision)\n"
            "• <code>.models</code> / <code>.active</code> — Active models overview\n"
            "• <code>.act</code> — Live latency & ping test\n"
            "• <code>.1gpt on / off / status</code> — Per-chat: .gpt 'Thinking...' ke baad naye message mein answer"
        ),
    },
    "media": {
        "title": "🎵 Media & Content Grabber",
        "description": (
            "🎵 <b>Media, Music & Content Grabber</b>\n\n"
            "• <code>.g [link]</code> / <code>.grab [link]</code> — Grab restricted media/files from private GC link or reply to bot DM\n"
            "• <code>.song &lt;name&gt;</code> / <code>.music</code> — Download 320kbps MP3\n"
            "• <code>.del [n]</code> / <code>.deleted</code> — Anti-delete logger\n"
            "• <code>.kang [emoji]</code> — Kang stickers / GIFs / media\n"
            "• <code>.packs</code> — Sticker pack links & stats\n"
            "• <code>.q</code> / <code>.qf</code> / <code>.qr</code> — Quotly sticker generator\n"
            "• <code>.tts &lt;text&gt;</code> — Natural voice note audio\n"
            "• <code>.tr [lang]</code> — Translate text & audio"
        ),
    },
    "memory": {
        "title": "🧠 Memory & Auto-Reply",
        "description": (
            "🧠 <b>Memory & Auto-Reply</b>\n\n"
            "• <code>.yaad &lt;text&gt;</code> — Save memory\n"
            "• <code>.yaadein</code> / <code>.memories</code> — View all memories\n"
            "• <code>.bhool &lt;id/all&gt;</code> — Delete saved memory\n"
            "• <code>.on</code> / <code>.off</code> — AI auto-reply toggle\n"
        ),
    },
    "tools": {
        "title": "🛠️ Utilities & Tools",
        "description": (
            "🛠️ <b>Utilities & Tools</b>\n\n"
            "• <code>.afk [reason]</code> / <code>.unafk</code> — AFK status with mention alert\n"
            "• <code>.enc [msg]</code> / <code>.dec</code> — Secret chat AES encryption\n"
            "• <code>.setkey &lt;pass&gt;</code> / <code>.delkey</code> — Chat encryption key setup\n"
            "• <code>.info [user]</code> / <code>.whois</code> — User info card\n"
            "• <code>.ping</code> — Real-time latency\n"
            "• <code>.repo</code> — Official GitHub repository & deploy links\n"
            "• <code>,restart</code> — Full bot restart (Owner only)\n"
            "• <code>.update</code> — Auto-pull updates from GitHub & restart\n"
            "• <code>.stop</code> — Sab running kaam aur queue cancel (silent, group mein output nahi)\n"
            "• <code>.start</code> — Bot intro & status\n"
            "• <code>.inline [q]</code> — Trigger inline queries\n"
            "• <code>.help [cat]</code> / <code>.cmd</code> — Commands menu"
        ),
    },
    "sudo": {
        "title": "🛡️ Sudo & Groups",
        "description": (
            "🛡️ <b>Sudo & Group Management</b>\n\n"
            "• <code>.gcauth [chat_id]</code> — Authorize group chat\n"
            "• <code>.gcunauth [chat_id]</code> — Revoke group chat authorization\n"
            "• <code>.gclist</code> — List authorized groups\n"
            "• <code>.auth [user_id]</code> / <code>.unauth</code> — Authorize user DM\n"
            "• <code>.users</code> — List authorized users\n"
            "• <code>.sudo [user_id]</code> / <code>.unsudo</code> — Grant / revoke sudo\n"
            "• <code>.sudousers</code> — List sudo users"
        ),
    },
}

CATEGORY_ALIASES: dict[str, str] = {
    "games": "games",
    "game": "games",
    "wordly": "games",
    "k": "games",
    "c": "games",
    "g": "games",
    "grab": "games",
    "grabber": "games",
    "humanize": "games",
    "gcreply": "games",
    "ai": "ai",
    "gpt": "ai",
    "model": "ai",
    "models": "ai",
    "media": "media",
    "music": "media",
    "song": "media",
    "songs": "media",
    "stickers": "media",
    "sticker": "media",
    "kang": "media",
    "quotly": "media",
    "quote": "media",
    "tts": "media",
    "tr": "media",
    "translate": "media",
    "del": "media",
    "deleted": "media",
    "memory": "memory",
    "memories": "memory",
    "yaad": "memory",
    "yaadein": "memory",
    "bhool": "memory",
    "autoreply": "memory",
    "reply": "memory",
    "tools": "tools",
    "tool": "tools",
    "util": "tools",
    "utils": "tools",
    "enc": "tools",
    "dec": "tools",
    "crypto": "tools",
    "encryption": "tools",
    "afk": "tools",
    "info": "tools",
    "whois": "tools",
    "ping": "tools",
    "repo": "tools",
    "github": "tools",
    "update": "tools",
    "restart": "tools",
    "sudo": "sudo",
    "sudos": "sudo",
    "auth": "sudo",
    "gc": "sudo",
    "group": "sudo",
    "groups": "sudo",
    "gcauth": "sudo",
    "inline": "tools",
    "start": "tools",
    "alive": "tools",
    "all": "all",
    "full": "all",
}

def resolve_category_alias(name: str | None) -> str | None:
    """Resolves a category name or alias to canonical category key."""
    if not name:
        return None
    return CATEGORY_ALIASES.get(name.lower().strip())

def get_help_main_text() -> str:
    """Returns minimalist, non-spammy main menu text."""
    return (
        "🐍 <b>Viper Userbot — Commands Hub</b>\n\n"
        "<i>Select a category below:</i>"
    )

def get_category_text(cat_key: str) -> str:
    """Returns detailed text for a single category or all categories."""
    if cat_key == "all":
        return get_all_commands_text()
    cat = HELP_CATEGORIES.get(cat_key)
    if cat:
        return cat["description"]
    return get_help_main_text()

def get_all_commands_text() -> str:
    """Full comprehensive list of all bot commands."""
    sections = [
        "📜 <b>All Commands Overview — Complete List</b>\n",
    ]
    for key, data in HELP_CATEGORIES.items():
        sections.append(data["description"])
    return "\n\n━━━━━━━━━━━━━━━\n\n".join(sections)

def get_fallback_menu_text() -> str:
    """
    Fallback text for Telethon userbot chats where the official bot account
    cannot send inline buttons (e.g. private groups where bot is not added).
    """
    return (
        "🐍 <b>Viper Userbot — Commands Hub</b>\n\n"
        "• <code>.help games</code> — 🎮 Games & Solvers\n"
        "• <code>.help ai</code> — 🤖 AI Assistant\n"
        "• <code>.help media</code> — 🎵 Media & Music\n"
        "• <code>.help memory</code> — 🧠 Memory & Reply\n"
        "• <code>.help tools</code> — 🛠️ Utilities\n"
        "• <code>.help sudo</code> — 🛡️ Sudo & Groups\n"
        "• <code>.help all</code> — 📋 Full list"
    )

def get_help_main_markup() -> list[list[dict[str, str]]]:
    """Returns main menu inline keyboard with 2-column layout."""
    return [
        [
            {"text": "🎮 Game Solvers", "callback_data": "help_cat_games"},
            {"text": "🤖 AI Assistant", "callback_data": "help_cat_ai"},
        ],
        [
            {"text": "🎵 Media & Music", "callback_data": "help_cat_media"},
            {"text": "🧠 Memory & Reply", "callback_data": "help_cat_memory"},
        ],
        [
            {"text": "🛠️ Utilities & Tools", "callback_data": "help_cat_tools"},
            {"text": "🛡️ Sudo & Groups", "callback_data": "help_cat_sudo"},
        ],
        [
            {"text": "📋 All Commands", "callback_data": "help_cat_all"},
            {"text": "❌ Close Menu", "callback_data": "help_close"},
        ],
    ]

def get_category_markup(cat_key: str | None = None) -> list[list[dict[str, str]]]:
    """Returns navigation inline keyboard for a specific category view."""
    return [
        [
            {"text": "🔙 Back to Menu", "callback_data": "help_main"},
            {"text": "❌ Close", "callback_data": "help_close"},
        ]
    ]
