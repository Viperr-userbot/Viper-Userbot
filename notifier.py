import httpx
import os
import asyncio
import config
import database
import logging

log = logging.getLogger("hwh.notifier")

API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"
OWNER_ID = int(getattr(config, "OWNER_ID", 0) or 0)

def resolve_delivery_targets(extra_chat_id: int | None = None, target_user_id: int | None = None) -> list[int]:
    """
    Resolves the list of chat IDs to receive bot messages:
    - If target_user_id is passed, the user DM goes ONLY to that specific user (owner or sudo).
    - If extra_chat_id is passed, it is only included if the GC is authorized for that specific user.
    """
    targets = []
    if target_user_id:
        targets.append(int(target_user_id))
    else:
        if OWNER_ID:
            targets.append(OWNER_ID)

    if extra_chat_id:
        cid = int(extra_chat_id)
        if target_user_id:
            if database.is_auth_gc_for_user(cid, int(target_user_id)):
                if cid not in targets:
                    targets.append(cid)
        else:
            if database.is_auth_gc(cid):
                if cid not in targets:
                    targets.append(cid)

    return targets

async def get_all_targets() -> list[int]:
    """Returns default broadcast targets (owner + non-sudo promoted users). Kept for compatibility."""
    targets = []
    if OWNER_ID:
        targets.append(OWNER_ID)
    sudo_set = database.get_sudo_users()
    promoted = database.get_promoted_users()
    for uid in promoted:
        if uid not in sudo_set and uid not in targets:
            targets.append(uid)
    return targets

async def text(msg: str, extra_chat_id: int | None = None, target_user_id: int | None = None):
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return

    async with httpx.AsyncClient(timeout=30) as c:
        tasks = []
        for chat_id in targets:
            tasks.append(c.post(f"{API}/sendMessage", data={
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for chat_id, res in zip(targets, results):
            if isinstance(res, Exception):
                log.error(f"Failed to send text to {chat_id}: {res}")
            elif res.status_code != 200:
                log.warning(f"Failed to send text to {chat_id}: {res.text}")

async def photo(png: bytes, caption: str = "", extra_chat_id: int | None = None, target_user_id: int | None = None) -> bool:
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return False

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None
    success = False

    async with httpx.AsyncClient(timeout=180) as c:
        try:
            r = await c.post(
                f"{API}/sendPhoto",
                data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": ("image.png", png, "image/png")},
            )
            if r.status_code == 200:
                success = True
                data = r.json()
                try:
                    file_id = data["result"]["photo"][-1]["file_id"]
                except (KeyError, IndexError):
                    pass
            else:
                log.warning(f"Failed to send photo to {first_target}: {r.text}")
        except Exception as e:
            log.error(f"Error sending photo to {first_target}: {e}")

        if success and rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(
                        f"{API}/sendPhoto",
                        data={"chat_id": chat_id, "photo": file_id, "caption": caption[:1024], "parse_mode": "HTML"}
                    ))
                else:
                    tasks.append(c.post(
                        f"{API}/sendPhoto",
                        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                        files={"photo": ("image.png", png, "image/png")},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

    return success

async def send_to_chat(chat_id: int, msg: str) -> bool:
    """Directly sends a text message to a specific chat/GC via bot token."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{API}/sendMessage", data={
                "chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            return r.status_code == 200
    except Exception as e:
        log.error(f"Failed to send_to_chat {chat_id}: {e}")
        return False

async def send_photo_to_chat(chat_id: int, png: bytes, caption: str = "") -> bool:
    """Directly sends a photo to a specific chat/GC via bot token."""
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{API}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"photo": ("image.png", png, "image/png")},
            )
            return r.status_code == 200
    except Exception as e:
        log.error(f"Failed to send_photo_to_chat {chat_id}: {e}")
        return False

async def sticker(raw: bytes, filename: str = "sticker.webp", mime_type: str = "image/webp", extra_chat_id: int | None = None, target_user_id: int | None = None):
    """Sends static, animated (TGS), or video (WebM) sticker natively to target(s)."""
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return

    if raw.startswith(b"\x1f\x8b") or filename.endswith(".tgs"):
        filename = "sticker.tgs"
        mime_type = "application/x-tgsticker"
    elif raw.startswith(b"\x1a\x45\xdf\xa3") or filename.endswith(".webm"):
        filename = "sticker.webm"
        mime_type = "video/webm"
    elif not filename.endswith(".webp"):
        filename = "sticker.webp"
        mime_type = "image/webp"

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None

    async with httpx.AsyncClient(timeout=60) as c:
        try:
            r = await c.post(
                f"{API}/sendSticker",
                data={"chat_id": first_target},
                files={"sticker": (filename, raw, mime_type)},
            )
            if r.status_code == 200:
                data = r.json()
                file_id = data.get("result", {}).get("sticker", {}).get("file_id")
            else:
                log.warning(f"Failed to send sticker to {first_target} ({r.status_code}): {r.text}, falling back to sendDocument")
                await c.post(
                    f"{API}/sendDocument",
                    data={"chat_id": first_target, "caption": "📦 Sticker"},
                    files={"document": (filename, raw, mime_type)},
                )
        except Exception as e:
            log.error(f"Error sending sticker to {first_target}: {e}")

        if rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(f"{API}/sendSticker", data={"chat_id": chat_id, "sticker": file_id}))
                else:
                    tasks.append(c.post(
                        f"{API}/sendSticker",
                        data={"chat_id": chat_id},
                        files={"sticker": (filename, raw, mime_type)},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

async def animation(data: bytes, filename: str = "animation.mp4", caption: str = "", extra_chat_id: int | None = None, target_user_id: int | None = None):
    """Sends an animated GIF or looping MP4 via sendAnimation."""
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None

    async with httpx.AsyncClient(timeout=60) as c:
        try:
            r = await c.post(
                f"{API}/sendAnimation",
                data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"animation": (filename, data, "video/mp4")},
            )
            if r.status_code == 200:
                data = r.json()
                file_id = data.get("result", {}).get("animation", {}).get("file_id")
            else:
                log.warning(f"sendAnimation failed to {first_target} ({r.status_code}): {r.text}, falling back to sendDocument")
                await c.post(
                    f"{API}/sendDocument",
                    data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"document": (filename, data, "video/mp4")},
                )
        except Exception as e:
            log.error(f"Error sending animation to {first_target}: {e}")

        if rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(f"{API}/sendAnimation", data={"chat_id": chat_id, "animation": file_id, "caption": caption[:1024], "parse_mode": "HTML"}))
                else:
                    tasks.append(c.post(
                        f"{API}/sendAnimation",
                        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                        files={"animation": (filename, data, "video/mp4")},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

async def voice(ogg: bytes, caption: str = "", extra_chat_id: int | None = None, target_user_id: int | None = None) -> bool:
    """Sends a voice message (.ogg) natively to target(s) via sendVoice."""
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return False

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None
    success = False

    async with httpx.AsyncClient(timeout=180) as c:
        try:
            r = await c.post(
                f"{API}/sendVoice",
                data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"voice": ("voice.ogg", ogg, "audio/ogg")},
            )
            if r.status_code == 200:
                success = True
                data = r.json()
                file_id = data.get("result", {}).get("voice", {}).get("file_id")
            else:
                log.warning(f"Failed to send voice to {first_target}: {r.text}")
        except Exception as e:
            log.error(f"Error sending voice to {first_target}: {e}")

        if success and rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(f"{API}/sendVoice", data={"chat_id": chat_id, "voice": file_id, "caption": caption[:1024], "parse_mode": "HTML"}))
                else:
                    tasks.append(c.post(
                        f"{API}/sendVoice",
                        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                        files={"voice": ("voice.ogg", ogg, "audio/ogg")},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

    return success

async def video(data: bytes, filename: str = "video.mp4", caption: str = "", extra_chat_id: int | None = None, target_user_id: int | None = None) -> bool:
    """Sends a video natively to target(s) via sendVideo."""
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return False

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None
    success = False

    async with httpx.AsyncClient(timeout=180) as c:
        try:
            r = await c.post(
                f"{API}/sendVideo",
                data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"video": (filename, data, "video/mp4")},
            )
            if r.status_code == 200:
                success = True
                data = r.json()
                file_id = data.get("result", {}).get("video", {}).get("file_id")
            else:
                r2 = await c.post(
                    f"{API}/sendDocument",
                    data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"document": (filename, data, "video/mp4")},
                )
                if r2.status_code == 200:
                    success = True
                    data2 = r2.json()
                    file_id = data2.get("result", {}).get("document", {}).get("file_id")
                else:
                    log.warning(f"Failed to send video to {first_target}: {r.text} | {r2.text}")
        except Exception as e:
            log.error(f"Error sending video to {first_target}: {e}")

        if success and rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(f"{API}/sendVideo", data={"chat_id": chat_id, "video": file_id, "caption": caption[:1024], "parse_mode": "HTML"}))
                else:
                    tasks.append(c.post(
                        f"{API}/sendVideo",
                        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                        files={"video": (filename, data, "video/mp4")},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

    return success

async def video_note(data: bytes, extra_chat_id: int | None = None, target_user_id: int | None = None) -> bool:
    """Sends a round video note (.mp4) natively to target(s) via sendVideoNote."""
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return False

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None
    success = False

    async with httpx.AsyncClient(timeout=180) as c:
        try:
            r = await c.post(
                f"{API}/sendVideoNote",
                data={"chat_id": first_target},
                files={"video_note": ("video_note.mp4", data, "video/mp4")},
            )
            if r.status_code == 200:
                success = True
                data_json = r.json()
                file_id = data_json.get("result", {}).get("video_note", {}).get("file_id")
            else:
                log.warning(f"sendVideoNote failed to {first_target}: {r.text}, falling back to sendVideo")
                return await video(data, filename="video_note.mp4", extra_chat_id=extra_chat_id, target_user_id=target_user_id)
        except Exception as e:
            log.error(f"Error sending video note to {first_target}: {e}")
            return await video(data, filename="video_note.mp4", extra_chat_id=extra_chat_id, target_user_id=target_user_id)

        if success and rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(f"{API}/sendVideoNote", data={"chat_id": chat_id, "video_note": file_id}))
                else:
                    tasks.append(c.post(
                        f"{API}/sendVideoNote",
                        data={"chat_id": chat_id},
                        files={"video_note": ("video_note.mp4", data, "video/mp4")},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

    return success

async def audio(data: bytes, filename: str = "audio.mp3", caption: str = "", extra_chat_id: int | None = None, target_user_id: int | None = None) -> bool:
    """Sends an audio file natively to target(s) via sendAudio."""
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return False

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None
    success = False

    async with httpx.AsyncClient(timeout=180) as c:
        try:
            r = await c.post(
                f"{API}/sendAudio",
                data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"audio": (filename, data, "audio/mpeg")},
            )
            if r.status_code == 200:
                success = True
                data_json = r.json()
                file_id = data_json.get("result", {}).get("audio", {}).get("file_id")
            else:
                log.warning(f"Failed to send audio to {first_target}: {r.text}")
        except Exception as e:
            log.error(f"Error sending audio to {first_target}: {e}")

        if success and rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(f"{API}/sendAudio", data={"chat_id": chat_id, "audio": file_id, "caption": caption[:1024], "parse_mode": "HTML"}))
                else:
                    tasks.append(c.post(
                        f"{API}/sendAudio",
                        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                        files={"audio": (filename, data, "audio/mpeg")},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

    return success

async def document(data: bytes, filename: str = "file", caption: str = "", extra_chat_id: int | None = None, target_user_id: int | None = None) -> bool:
    """Sends a document/file to target(s) via sendDocument."""
    targets = resolve_delivery_targets(extra_chat_id, target_user_id)
    if not targets:
        return False

    first_target = targets[0]
    rest_targets = targets[1:]
    file_id = None
    success = False

    async with httpx.AsyncClient(timeout=180) as c:
        try:
            r = await c.post(
                f"{API}/sendDocument",
                data={"chat_id": first_target, "caption": caption[:1024], "parse_mode": "HTML"},
                files={"document": (filename, data)},
            )
            if r.status_code == 200:
                success = True
                data_json = r.json()
                file_id = data_json.get("result", {}).get("document", {}).get("file_id")
            else:
                log.warning(f"Failed to send document to {first_target}: {r.text}")
        except Exception as e:
            log.error(f"Error sending document to {first_target}: {e}")

        if success and rest_targets:
            tasks = []
            for chat_id in rest_targets:
                if file_id:
                    tasks.append(c.post(f"{API}/sendDocument", data={"chat_id": chat_id, "document": file_id, "caption": caption[:1024], "parse_mode": "HTML"}))
                else:
                    tasks.append(c.post(
                        f"{API}/sendDocument",
                        data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
                        files={"document": (filename, data)},
                    ))
            await asyncio.gather(*tasks, return_exceptions=True)

    return success
