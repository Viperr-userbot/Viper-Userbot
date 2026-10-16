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
        "title": "🎵 Media & Music Tools",
        "description": (
            "🎵 <b>Media, Music & Anti-Delete Tools</b>\n\n"
            "• <code>.tts &lt;text&gt;</code> — Natural voice synthesis (APInex Aura-2)\n"
            "• <code>.stt</code> — Transcribe replied voice / audio note (APInex Nova)\n"
            "• <code>.tr [lang] &lt;text&gt;</code> — Fast 112-language translator & speech translator\n"
            "• <code>.song &lt;name&gt;</code> / <code>.music</code> — Download 320kbps MP3\n"
            "• <code>.rmbg [-png]</code> — Remove background (Instant sticker, pure transparent, zero text)\n"
            "• <code>.txt [:style] &lt;text&gt;</code> / <code>.txts</code> — Text-to-sticker (40+ styles, Hindi/Devanagari, pack mode)\n"
            "• <code>.g [link]</code> / <code>.grab [link]</code> — Grab restricted media/files from private GC link or reply to bot DM\n"
            "• <code>.del [n]</code> / <code>.deleted</code> — Anti-delete logger\n"
            "• <code>.kang [emoji]</code> — Kang stickers / GIFs / media\n"
            "• <code>.packs</code> — Sticker pack links & stats\n"
            "• <code>.q</code> / <code>.qf</code> / <code>.qr</code> — Quotly sticker generator\n"
            "• <code>.waifu [art]</code> / <code>.anime</code> — Anime directory & random waifu artwork\n"
            "• <code>.hug / .pat / .kiss / .slap / .bonk</code> — 30 anime reaction GIF animations"
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
            "• <code>.clone [reply/@user]</code> — Target profile (Name, Bio, DP) clone karein (Owner only)\n"
            "• <code>.revert</code> / <code>.unclone</code> — Original profile wapas restore karein (Owner only)\n"
            "• <code>.enc [msg]</code> / <code>.dec</code> — Secret chat AES encryption\n"
            "• <code>.setkey &lt;pass&gt;</code> / <code>.delkey</code> / <code>.getkey</code> — Chat encryption key setup\n"
            "• <code>.info [user]</code> / <code>.whois</code> — User info card\n"
            "• <code>.ping</code> — Real-time latency\n"
            "• <code>.repo</code> — Official GitHub repository & deploy links\n"
            "• <code>,restart</code> / <code>.restart</code> — Full bot restart (Owner only)\n"
            "• <code>.update</code> / <code>.update force</code> — Auto-pull updates from GitHub & restart\n"
            "• <code>.stop</code> — Sab running kaam aur queue cancel (silent, group mein output nahi)\n"
            "• <code>.start</code> / <code>.alive</code> — Bot intro & status\n"
            "• <code>.inline [q]</code> — Trigger inline queries\n"
            "• <code>.help [cat]</code> / <code>.cmd [cat]</code> — Commands menu"
        ),
    },
    "sudo": {
        "title": "🛡️ Sudo & Groups",
        "description": (
            "🛡️ <b>Sudo & Group Management</b>\n\n"
            "• <code>.gcauth [chat_id]</code> / <code>.authgc</code> — Authorize group chat\n"
            "• <code>.gcunauth [chat_id]</code> / <code>.unauthgc</code> — Revoke group chat authorization\n"
            "• <code>.gclist</code> / <code>.gcauths</code> — List authorized groups\n"
            "• <code>.auth [user_id]</code> / <code>.promote</code> — Authorize user DM\n"
            "• <code>.unauth [user_id]</code> / <code>.demote</code> — Revoke user DM authorization\n"
            "• <code>.users</code> / <code>.authed</code> / <code>.promoted</code> — List authorized users\n"
            "• <code>.sudo [user_id]</code> / <code>.unsudo</code> — Grant / revoke sudo (Owner only)\n"
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
    "1gpt": "ai",
    "model": "ai",
    "models": "ai",
    "act": "ai",
    "media": "media",
    "music": "media",
    "song": "media",
    "songs": "media",
    "stickers": "media",
    "sticker": "media",
    "kang": "media",
    "pack": "media",
    "packs": "media",
    "quotly": "media",
    "quote": "media",
    "q": "media",
    "qf": "media",
    "qr": "media",
    "qtrf": "media",
    "tts": "media",
    "stt": "media",
    "voice": "media",
    "tr": "media",
    "translate": "media",
    "del": "media",
    "deleted": "media",
    "rmbg": "media",
    "bg": "media",
    "removebg": "media",
    "txt": "media",
    "text": "media",
    "tsticker": "media",
    "txts": "media",
    "txtstyles": "media",
    "waifu": "media",
    "anime": "media",
    "reaction": "media",
    "reactions": "media",
    "hug": "media",
    "pat": "media",
    "kiss": "media",
    "cuddle": "media",
    "handhold": "media",
    "feed": "media",
    "tickle": "media",
    "poke": "media",
    "bite": "media",
    "wink": "media",
    "smug": "media",
    "pout": "media",
    "smile": "media",
    "wave": "media",
    "dance": "media",
    "laugh": "media",
    "highfive": "media",
    "cheers": "media",
    "slap": "media",
    "bonk": "media",
    "punch": "media",
    "yeet": "media",
    "stare": "media",
    "run": "media",
    "blush": "media",
    "cry": "media",
    "sleep": "media",
    "think": "media",
    "shrug": "media",
    "nom": "media",
    "memory": "memory",
    "memories": "memory",
    "yaad": "memory",
    "yaadein": "memory",
    "bhool": "memory",
    "forget": "memory",
    "bhoolja": "memory",
    "autoreply": "memory",
    "reply": "memory",
    "on": "memory",
    "off": "memory",
    "tools": "tools",
    "tool": "tools",
    "util": "tools",
    "utils": "tools",
    "enc": "tools",
    "dec": "tools",
    "crypto": "tools",
    "encryption": "tools",
    "setkey": "tools",
    "delkey": "tools",
    "getkey": "tools",
    "afk": "tools",
    "unafk": "tools",
    "info": "tools",
    "whois": "tools",
    "ping": "tools",
    "clone": "tools",
    "revert": "tools",
    "unclone": "tools",
    "repo": "tools",
    "github": "tools",
    "update": "tools",
    "restart": "tools",
    "stop": "tools",
    "sudo": "sudo",
    "sudos": "sudo",
    "sudousers": "sudo",
    "auth": "sudo",
    "unauth": "sudo",
    "promote": "sudo",
    "demote": "sudo",
    "users": "sudo",
    "authed": "sudo",
    "promoted": "sudo",
    "gc": "sudo",
    "group": "sudo",
    "groups": "sudo",
    "gcauth": "sudo",
    "authgc": "sudo",
    "gcunauth": "sudo",
    "unauthgc": "sudo",
    "gclist": "sudo",
    "gcauths": "sudo",
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
        "• <code>.cmd games</code> — 🎮 Games & Solvers (Wordly, Hunt, 10Things)\n"
        "• <code>.cmd ai</code> — 🤖 AI Assistant (.gpt, models, latency)\n"
        "• <code>.cmd media</code> — 🎵 Media, TTS, STT, Songs, Stickers, Waifu\n"
        "• <code>.cmd memory</code> — 🧠 Memory & AI Auto-Reply\n"
        "• <code>.cmd tools</code> — 🛠️ Utilities (Clone, AFK, Enc, Ping, Update)\n"
        "• <code>.cmd sudo</code> — 🛡️ Sudo & Group Management\n"
        "• <code>.cmd all</code> — 📋 Complete Commands Overview\n\n"
        "💡 <i>Tip: Detailed view ke liye <code>.cmd &lt;category&gt;</code> ya <code>.cmd all</code> likhein.</i>"
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
