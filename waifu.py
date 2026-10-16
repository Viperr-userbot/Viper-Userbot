"""
Anime Reaction & Waifu Engine for Telegram Userbot
--------------------------------------------------
Multi-source anime reaction engine supporting 30 categories with
parallel racing (nekos.best, otakugifs, purrbot, waifu.pics) and
an in-memory prefetch pool for instant <200ms responses.
Also supports random high-res waifu artwork fetching.
"""

import html
import random
import asyncio
import logging
from typing import TypedDict

import httpx

log = logging.getLogger("hwh.waifu")

REACTION_CATEGORIES = [
    # Affection
    "hug", "pat", "kiss", "cuddle", "handhold", "feed",
    # Playful
    "tickle", "poke", "bite", "wink", "smug", "pout",
    # Social & Fun
    "smile", "wave", "dance", "laugh", "highfive", "cheers",
    # Action & Drama
    "slap", "bonk", "punch", "yeet", "stare", "run",
    # Emotions & Mood
    "blush", "cry", "sleep", "think", "shrug", "nom",
]

class ReactionMeta(TypedDict):
    emoji: str
    verb: str
    action: str
    category: str

REACTION_TITLES: dict[str, ReactionMeta] = {
    # Affection
    "hug": {"emoji": "🤗", "verb": "hugs", "action": "gives a warm, affectionate hug to", "category": "Affection"},
    "pat": {"emoji": "💆", "verb": "pats", "action": "gently headpats", "category": "Affection"},
    "kiss": {"emoji": "💋", "verb": "kisses", "action": "gives a sweet, tender kiss to", "category": "Affection"},
    "cuddle": {"emoji": "🥰", "verb": "cuddles", "action": "cuddles closely with", "category": "Affection"},
    "handhold": {"emoji": "🤝", "verb": "holds hands with", "action": "tenderly holds hands with", "category": "Affection"},
    "feed": {"emoji": "🍰", "verb": "feeds", "action": "feeds delicious treats to", "category": "Affection"},

    # Playful
    "tickle": {"emoji": "🪶", "verb": "tickles", "action": "tickles playfully", "category": "Playful"},
    "poke": {"emoji": "👉", "verb": "pokes", "action": "curiously pokes", "category": "Playful"},
    "bite": {"emoji": "🦷", "verb": "bites", "action": "playfully nibbles on", "category": "Playful"},
    "wink": {"emoji": "😉", "verb": "winks at", "action": "playfully winks at", "category": "Playful"},
    "smug": {"emoji": "😏", "verb": "smugs at", "action": "flashes a cheeky smug grin at", "category": "Playful"},
    "pout": {"emoji": "🥺", "verb": "pouts at", "action": "pouts adorably at", "category": "Playful"},

    # Social & Fun
    "smile": {"emoji": "😊", "verb": "smiles at", "action": "beams a radiant smile at", "category": "Social"},
    "wave": {"emoji": "👋", "verb": "waves at", "action": "happily waves at", "category": "Social"},
    "dance": {"emoji": "💃", "verb": "dances with", "action": "joyfully dances alongside", "category": "Social"},
    "laugh": {"emoji": "😂", "verb": "laughs with", "action": "laughs wholeheartedly with", "category": "Social"},
    "highfive": {"emoji": "✋", "verb": "high-fives", "action": "enthusiastically high-fives", "category": "Social"},
    "cheers": {"emoji": "🥂", "verb": "cheers with", "action": "clinks glasses and cheers with", "category": "Social"},

    # Action & Drama
    "slap": {"emoji": "👋", "verb": "slaps", "action": "delivers a dramatic anime slap to", "category": "Action"},
    "bonk": {"emoji": "🔨", "verb": "bonks", "action": "bonks with a squeaky hammer on", "category": "Action"},
    "punch": {"emoji": "👊", "verb": "punches", "action": "lands a cinematic anime punch on", "category": "Action"},
    "yeet": {"emoji": "🚀", "verb": "yeets", "action": "yeets into the stratosphere", "category": "Action"},
    "stare": {"emoji": "👀", "verb": "stares at", "action": "stares intensely at", "category": "Action"},
    "run": {"emoji": "🏃", "verb": "runs away from", "action": "sprints away dramatically from", "category": "Action"},

    # Emotions & Mood
    "blush": {"emoji": "😳", "verb": "blushes at", "action": "turns crimson red and blushes at", "category": "Mood"},
    "cry": {"emoji": "😭", "verb": "cries before", "action": "bursts into dramatic tears before", "category": "Mood"},
    "sleep": {"emoji": "💤", "verb": "sleeps beside", "action": "falls asleep peacefully beside", "category": "Mood"},
    "think": {"emoji": "🤔", "verb": "thinks about", "action": "ponders deeply with", "category": "Mood"},
    "shrug": {"emoji": "🤷", "verb": "shrugs at", "action": "gives a casual anime shrug to", "category": "Mood"},
    "nom": {"emoji": "🍙", "verb": "noms with", "action": "munches and noms snacks with", "category": "Mood"},
}

# In-memory reaction prefetch pool for 0ms instant replies
_reaction_pool: dict[str, list[dict]] = {}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def build_waifu_directory_text() -> str:
    """Builds the stylish HTML Anime Reaction Directory text."""
    return (
        "<blockquote>🌸 <b>ANIME REACTION DIRECTORY</b> • <i>Waifu Studio</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Total <b>30 Anime Reaction Commands</b> available!\n"
        "Reply to any user or use directly in chats:\n\n"
        "💖 <b>Affection:</b>\n"
        "• <code>.hug</code> • <code>.pat</code> • <code>.kiss</code> • <code>.cuddle</code> • <code>.handhold</code> • <code>.feed</code>\n\n"
        "🌸 <b>Playful:</b>\n"
        "• <code>.tickle</code> • <code>.poke</code> • <code>.bite</code> • <code>.wink</code> • <code>.smug</code> • <code>.pout</code>\n\n"
        "✨ <b>Social & Fun:</b>\n"
        "• <code>.smile</code> • <code>.wave</code> • <code>.dance</code> • <code>.laugh</code> • <code>.highfive</code> • <code>.cheers</code>\n\n"
        "💥 <b>Action & Drama:</b>\n"
        "• <code>.slap</code> • <code>.bonk</code> • <code>.punch</code> • <code>.yeet</code> • <code>.stare</code> • <code>.run</code>\n\n"
        "🎭 <b>Emotions & Vibe:</b>\n"
        "• <code>.blush</code> • <code>.cry</code> • <code>.sleep</code> • <code>.think</code> • <code>.shrug</code> • <code>.nom</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎲 <i>Use <code>.waifu art</code> for random high-res anime art!</i>\n"
        "⚡ <i>Multi-source anime GIFs from OtakuGIFs, PurrBot & Nekos!</i></blockquote>"
    )


def format_reaction_caption(
    cmd: str,
    sender_name: str,
    sender_id: int,
    target_name: str | None = None,
    target_id: int | None = None,
) -> str:
    """Formats HTML caption for anime reactions."""
    info = REACTION_TITLES.get(cmd, {
        "emoji": "✨",
        "verb": cmd,
        "action": f"performs a {cmd} on",
        "category": "Social",
    })

    safe_sname = html.escape(sender_name or "Someone")
    emoji = info["emoji"]

    if target_name and target_id and target_id != sender_id:
        safe_tname = html.escape(target_name)
        action_text = info["action"]
        return (
            f"<blockquote>{emoji} <b><a href=\"tg://user?id={sender_id}\">{safe_sname}</a></b> "
            f"<i>{action_text}</i> "
            f"<b><a href=\"tg://user?id={target_id}\">{safe_tname}</a></b>! 💕</blockquote>"
        )
    else:
        verb_text = info["verb"]
        return (
            f"<blockquote>{emoji} <b><a href=\"tg://user?id={sender_id}\">{safe_sname}</a></b> "
            f"<i>{verb_text} everyone with radiant anime energy!</i> 💕</blockquote>"
        )


async def _fetch_from_nekos_best(client: httpx.AsyncClient, cat: str) -> dict | None:
    try:
        url = f"https://nekos.best/api/v2/{cat}"
        res = await client.get(url, timeout=3.5)
        if res.status_code == 200:
            data = res.json()
            results = data.get("results")
            if results and results[0].get("url"):
                return {
                    "url": results[0]["url"],
                    "anime_name": results[0].get("anime_name"),
                    "source": "nekos.best",
                    "category": cat
                }
    except Exception:
        pass
    return None


async def _fetch_from_otakugifs(client: httpx.AsyncClient, cat: str) -> dict | None:
    try:
        url = f"https://api.otakugifs.xyz/gif?reaction={cat}"
        res = await client.get(url, timeout=3.5)
        if res.status_code == 200:
            data = res.json()
            if data.get("url"):
                return {
                    "url": data["url"],
                    "source": "otakugifs",
                    "category": cat
                }
    except Exception:
        pass
    return None


async def _fetch_from_purrbot(client: httpx.AsyncClient, cat: str) -> dict | None:
    try:
        url = f"https://purrbot.site/api/img/sfw/{cat}/gif"
        res = await client.get(url, timeout=3.5)
        if res.status_code == 200:
            data = res.json()
            if data.get("link"):
                return {
                    "url": data["link"],
                    "source": "purrbot",
                    "category": cat
                }
    except Exception:
        pass
    return None


async def _fetch_from_waifupics(client: httpx.AsyncClient, cat: str) -> dict | None:
    try:
        url = f"https://api.waifu.pics/sfw/{cat}"
        res = await client.get(url, timeout=3.5)
        if res.status_code == 200:
            data = res.json()
            if data.get("url"):
                return {
                    "url": data["url"],
                    "source": "waifu.pics",
                    "category": cat
                }
    except Exception:
        pass
    return None


async def _race_reaction_sources(cat: str) -> dict | None:
    """Races multiple high-speed anime GIF providers in parallel."""
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        providers = [
            _fetch_from_nekos_best(client, cat),
            _fetch_from_otakugifs(client, cat),
            _fetch_from_purrbot(client, cat),
            _fetch_from_waifupics(client, cat),
        ]

        done, pending = await asyncio.wait(
            [asyncio.create_task(p) for p in providers],
            return_when=asyncio.FIRST_COMPLETED
        )

        winner = None
        for task in done:
            try:
                res = task.result()
                if res and res.get("url"):
                    winner = res
                    break
            except Exception:
                pass

        # If the first completed task failed, wait for remaining tasks
        if not winner and pending:
            done_rest, _ = await asyncio.wait(pending, timeout=2.5)
            for task in done_rest:
                try:
                    res = task.result()
                    if res and res.get("url"):
                        winner = res
                        break
                except Exception:
                    pass

        # Cancel any unfinished tasks
        for task in pending:
            if not task.done():
                task.cancel()

        return winner


async def fetch_waifu_reaction(category: str) -> dict | None:
    """
    Fetches an anime reaction GIF for the specified category.
    Uses in-memory prefetch pool for instant 0ms response when available,
    and races top anime CDNs in parallel.
    """
    cat = category.lower().strip()
    if cat not in REACTION_TITLES:
        cat = "hug"

    # 1. Check in-memory prefetch pool
    pool = _reaction_pool.get(cat)
    if pool and len(pool) > 0:
        item = pool.pop()

        # Replenish in background
        async def _replenish():
            try:
                new_item = await _race_reaction_sources(cat)
                if new_item:
                    _reaction_pool.setdefault(cat, []).append(new_item)
            except Exception:
                pass

        asyncio.create_task(_replenish())
        return item

    # 2. Race providers in parallel
    result = await _race_reaction_sources(cat)
    return result


async def fetch_random_waifu_artwork() -> dict | None:
    """
    Fetches random high-resolution waifu artwork from multiple safe sources.
    """
    sources = [
        # Source 1: nekos.life waifu
        ("nekos.life", "https://nekos.life/api/v2/img/waifu"),
        # Source 2: nekos.best neko
        ("nekos.best", "https://nekos.best/api/v2/neko"),
        # Source 3: waifu.im
        ("waifu.im", "https://api.waifu.im/search?is_nsfw=false"),
        # Source 4: waifu.pics
        ("waifu.pics", "https://api.waifu.pics/sfw/waifu"),
    ]

    shuffled = list(sources)
    random.shuffle(shuffled)

    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=6.0) as client:
        for src_name, url in shuffled:
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    img_url = None
                    artist = "Anime Artist"

                    if src_name == "nekos.life":
                        img_url = data.get("url")
                    elif src_name == "nekos.best":
                        results = data.get("results", [])
                        if results:
                            img_url = results[0].get("url")
                            artist = results[0].get("artist_name") or "Anime Artist"
                    elif src_name == "waifu.im":
                        images = data.get("images", [])
                        if images:
                            img_url = images[0].get("url")
                            artist = (images[0].get("artist") or {}).get("name") or "Anime Artist"
                    elif src_name == "waifu.pics":
                        img_url = data.get("url")

                    if img_url:
                        return {
                            "url": img_url,
                            "artist": artist,
                            "source": src_name
                        }
            except Exception as e:
                log.debug(f"Artwork fetcher {src_name} failed: {e}")

    return None


async def download_media_bytes(url: str) -> tuple[bytes, str] | None:
    """
    Downloads media from URL into memory bytes with appropriate filename.
    """
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=15.0) as client:
        try:
            res = await client.get(url)
            if res.status_code == 200:
                ext = "gif"
                ctype = res.headers.get("content-type", "").lower()
                if "video/mp4" in ctype or url.lower().endswith(".mp4"):
                    ext = "mp4"
                elif "image/png" in ctype or url.lower().endswith(".png"):
                    ext = "png"
                elif "image/webp" in ctype or url.lower().endswith(".webp"):
                    ext = "webp"
                elif "image/jpeg" in ctype or url.lower().endswith((".jpg", ".jpeg")):
                    ext = "jpg"
                return res.content, f"anime_reaction.{ext}"
        except Exception as e:
            log.warning(f"Failed to download reaction bytes from {url}: {e}")
    return None
