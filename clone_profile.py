import os
import html
import logging
import asyncio
from telethon import utils
from telethon.tl.types import User
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest, DeletePhotosRequest
import database
import config

log = logging.getLogger("hwh.clone")

BACKUP_DIR = "clone_backup"
BACKUP_DP_FILE = os.path.join(BACKUP_DIR, "self_dp.jpg")

_clone_lock = asyncio.Lock()


async def clone_user_profile(client, target, event=None) -> tuple[bool, str]:
    """
    Clones target user's first name, last name, about/bio, and profile photo.
    Saves an immutable backup of the original self profile on first clone.
    Returns (success: bool, status_message: str).
    """
    # Authorization check: Sudo users cannot clone; strictly OWNER ONLY
    if event is not None and not getattr(event, "out", False):
        sender_id = getattr(event, "sender_id", None)
        if not sender_id and hasattr(event, "message") and event.message:
            sender_id = getattr(event.message, "sender_id", None)
        if sender_id and (database.is_sudo_user(sender_id) or sender_id != config.OWNER_ID):
            return False, "⚠️ <b>Permission Denied:</b> Sirf bot owner hi profile clone kar sakte hain (sudo users are not permitted)."

    async with _clone_lock:
        # 1. Resolve Target Entity
        try:
            if isinstance(target, User):
                entity = target
            else:
                entity = await client.get_entity(target)
        except Exception as e:
            return False, f"❌ Target user nahi mila: <code>{html.escape(str(e))}</code>"

        if not isinstance(entity, User):
            return False, "❌ Sirf user profile ko clone kar sakte hain (Channels/Chats allow nahi hain)."

        # 2. Prevent Cloning Self
        try:
            me = await client.get_me()
        except Exception as e:
            return False, f"❌ Failed to fetch self user entity: <code>{html.escape(str(e))}</code>"

        if entity.id == me.id:
            return False, "⚠️ Aap apni hi profile ko clone nahi kar sakte!"

        # 3. Save Immutable Original Backup (if not already backed up)
        if not database.has_clone_backup():
            self_first = getattr(me, "first_name", "") or ""
            self_last = getattr(me, "last_name", "") or ""
            self_about = ""
            try:
                me_full = await client(GetFullUserRequest("me"))
                if me_full and me_full.full_user:
                    self_about = getattr(me_full.full_user, "about", "") or ""
            except Exception as e:
                log.warning(f"Could not fetch self bio for clone backup: {e}")

            os.makedirs(BACKUP_DIR, exist_ok=True)
            has_dp = False
            try:
                dp_res = await client.download_profile_photo("me", file=BACKUP_DP_FILE)
                if not dp_res or not os.path.exists(BACKUP_DP_FILE) or os.path.getsize(BACKUP_DP_FILE) == 0:
                    try:
                        photos = await client.get_profile_photos("me", limit=1)
                        if photos:
                            dp_res = await client.download_media(photos[0], file=BACKUP_DP_FILE)
                    except Exception as pe:
                        log.warning(f"Fallback get_profile_photos for self DP failed: {pe}")

                if dp_res and os.path.exists(BACKUP_DP_FILE) and os.path.getsize(BACKUP_DP_FILE) > 0:
                    has_dp = True
            except Exception as e:
                log.warning(f"Could not download self DP for clone backup: {e}")

            database.save_clone_backup(self_first, self_last, self_about, has_dp)
            log.info(f"Saved original profile backup: '{self_first} {self_last}', DP={has_dp}")

        # 4. Fetch Target Profile Details
        target_first = (getattr(entity, "first_name", "") or "").strip()
        target_last = (getattr(entity, "last_name", "") or "").strip()
        target_about = ""
        try:
            target_full = await client(GetFullUserRequest(entity.id))
            if target_full and target_full.full_user:
                target_about = getattr(target_full.full_user, "about", "") or ""
        except Exception as e:
            log.warning(f"Could not fetch target full user info: {e}")

        safe_first = target_first or "User"

        # 5. Update Name & Bio
        try:
            await client(UpdateProfileRequest(
                first_name=safe_first[:64],
                last_name=target_last[:64] if target_last else "",
                about=target_about
            ))
        except Exception as e:
            err_str = str(e)
            if "ABOUT_TOO_LONG" in err_str:
                try:
                    await client(UpdateProfileRequest(
                        first_name=safe_first[:64],
                        last_name=target_last[:64] if target_last else "",
                        about=(target_about or "")[:70]
                    ))
                except Exception as ex2:
                    return False, f"❌ Profile update error: <code>{html.escape(str(ex2))}</code>"
            else:
                return False, f"❌ Profile update error: <code>{html.escape(err_str)}</code>"

        # 6. Copy Target Profile Photo
        dp_updated = False
        os.makedirs(BACKUP_DIR, exist_ok=True)
        target_dp_path = os.path.join(BACKUP_DIR, f"target_dp_{entity.id}.jpg")
        try:
            # Download target photo to a local JPEG file
            dp_downloaded = await client.download_profile_photo(entity, file=target_dp_path)
            if not dp_downloaded or not os.path.exists(target_dp_path) or os.path.getsize(target_dp_path) == 0:
                # In-memory bytes fallback or get_profile_photos fallback
                try:
                    photos = await client.get_profile_photos(entity, limit=1)
                    if photos:
                        dp_downloaded = await client.download_media(photos[0], file=target_dp_path)
                except Exception as ex:
                    log.warning(f"Fallback get_profile_photos error: {ex}")

            # If download_profile_photo returned raw bytes directly (e.g. in test mocks)
            if isinstance(dp_downloaded, bytes) and (not os.path.exists(target_dp_path) or os.path.getsize(target_dp_path) == 0):
                with open(target_dp_path, "wb") as f:
                    f.write(dp_downloaded)

            if os.path.exists(target_dp_path) and os.path.getsize(target_dp_path) > 0:
                # Normalize image with PIL to guarantee standard RGB JPEG format
                try:
                    from PIL import Image
                    with Image.open(target_dp_path) as im:
                        im = im.convert("RGB")
                        im.save(target_dp_path, "JPEG", quality=95)
                except Exception as pe:
                    log.warning(f"PIL photo normalization notice: {pe}")

                uploaded = await client.upload_file(target_dp_path, file_name="profile_photo.jpg")
                await client(UploadProfilePhotoRequest(file=uploaded))
                dp_updated = True

                # Remove previous photos so only the cloned photo remains active
                try:
                    current_photos = await client.get_profile_photos("me")
                    if current_photos and len(current_photos) > 1:
                        old_photos = [utils.get_input_photo(p) for p in current_photos[1:] if p]
                        if old_photos:
                            await client(DeletePhotosRequest(id=old_photos))
                except Exception as de:
                    log.warning(f"Failed to delete previous photos after clone: {de}")
        except Exception as e:
            log.warning(f"Failed to copy target profile photo: {e}")
        finally:
            if os.path.exists(target_dp_path):
                try:
                    os.remove(target_dp_path)
                except Exception:
                    pass

        # 7. Update Cloned State in Database
        target_name = f"{target_first} {target_last}".strip() or safe_first
        database.set_clone_active(True, entity.id, target_name)

        res_msg = (
            "🎭 <b>ID Cloned Successfully!</b>\n"
            f"• <b>Target:</b> <code>{html.escape(target_name)}</code> (<code>{entity.id}</code>)\n"
            f"• <b>Name:</b> <code>{html.escape(safe_first)} {html.escape(target_last)}</code>\n"
            f"• <b>Bio:</b> <i>{html.escape(target_about or 'None')}</i>\n"
            f"• <b>Profile Picture:</b> {'✅ Copied' if dp_updated else '⚠️ Target has no public DP'}\n\n"
            "💡 <i>Original profile wapas lane ke liye</i> <code>.revert</code> <i>likhein.</i>"
        )
        return True, res_msg


async def revert_user_profile(client, event=None) -> tuple[bool, str]:
    """
    Restores original self profile (first name, last name, about/bio, and DP) from backup.
    Returns (success: bool, status_message: str).
    """
    # Authorization check: Sudo users cannot revert; strictly OWNER ONLY
    if event is not None and not getattr(event, "out", False):
        sender_id = getattr(event, "sender_id", None)
        if not sender_id and hasattr(event, "message") and event.message:
            sender_id = getattr(event.message, "sender_id", None)
        if sender_id and (database.is_sudo_user(sender_id) or sender_id != config.OWNER_ID):
            return False, "⚠️ <b>Permission Denied:</b> Sirf bot owner hi profile revert kar sakte hain (sudo users are not permitted)."

    async with _clone_lock:
        if not database.has_clone_backup():
            return False, "⚠️ <b>Koi backup nahi mila!</b> Pehle kisi profile ko <code>.clone</code> karein."

        backup = database.get_clone_backup()
        orig_first = (backup.get("first_name") or "User").strip()
        orig_last = (backup.get("last_name") or "").strip()
        orig_about = backup.get("about") or ""
        orig_has_dp = backup.get("has_dp", False)

        # 1. Restore Name & Bio
        try:
            await client(UpdateProfileRequest(
                first_name=orig_first[:64],
                last_name=orig_last[:64] if orig_last else "",
                about=orig_about
            ))
        except Exception as e:
            return False, f"❌ Name/Bio restore error: <code>{html.escape(str(e))}</code>"

        # 2. Restore Profile Photo
        dp_restored = False
        if orig_has_dp and os.path.exists(BACKUP_DP_FILE) and os.path.getsize(BACKUP_DP_FILE) > 0:
            try:
                uploaded = await client.upload_file(BACKUP_DP_FILE, file_name="self_dp.jpg")
                await client(UploadProfilePhotoRequest(file=uploaded))
                dp_restored = True

                # Delete cloned photo(s) after original is restored
                try:
                    current_photos = await client.get_profile_photos("me")
                    if current_photos and len(current_photos) > 1:
                        old_photos = [utils.get_input_photo(p) for p in current_photos[1:] if p]
                        if old_photos:
                            await client(DeletePhotosRequest(id=old_photos))
                except Exception as de:
                    log.warning(f"Failed to delete cloned photos after revert: {de}")
            except Exception as e:
                log.warning(f"Failed to restore original DP from backup: {e}")
        elif not orig_has_dp:
            # Self originally had no profile photo; delete all current photos
            try:
                current_photos = await client.get_profile_photos("me")
                if current_photos:
                    inp_photos = [utils.get_input_photo(p) for p in current_photos if p]
                    if inp_photos:
                        await client(DeletePhotosRequest(id=inp_photos))
            except Exception as e:
                log.warning(f"Failed to delete photos during revert: {e}")

        # 3. Clean Up State and Backup File
        database.clear_clone_backup()
        database.set_clone_active(False)

        if os.path.exists(BACKUP_DP_FILE):
            try:
                os.remove(BACKUP_DP_FILE)
            except Exception:
                pass

        dp_status = "✅ Restored" if dp_restored else ("Removed (no original DP)" if not orig_has_dp else "⚠️ Re-upload skipped")
        res_msg = (
            "✅ <b>Profile Reverted Successfully!</b>\n"
            f"• <b>Name:</b> <code>{html.escape(orig_first)} {html.escape(orig_last)}</code>\n"
            f"• <b>Bio:</b> <i>{html.escape(orig_about or 'None')}</i>\n"
            f"• <b>Profile Picture:</b> {dp_status}\n\n"
            "✨ <i>Aapka original account state restore ho gaya hai.</i>"
        )
        return True, res_msg


def get_clone_status() -> str:
    """Returns HTML status summary of profile cloning."""
    active = database.is_clone_active()
    has_backup = database.has_clone_backup()
    backup = database.get_clone_backup()

    if active:
        target_name = backup.get("target_name") or "Unknown"
        target_id = backup.get("target_id") or "Unknown"
        return (
            "🎭 <b>Clone Status: ACTIVE</b>\n\n"
            f"• <b>Currently Cloned Target:</b> <code>{html.escape(target_name)}</code> (<code>{target_id}</code>)\n"
            f"• <b>Original Backup:</b> {'✅ Saved' if has_backup else '❌ None'}\n"
            f"• <b>Original Name:</b> <code>{html.escape(backup.get('first_name', ''))} {html.escape(backup.get('last_name', ''))}</code>\n"
            f"• <b>Original DP:</b> {'Yes' if backup.get('has_dp') else 'No'}\n\n"
            "💡 Use <code>.revert</code> to restore original profile."
        )
    elif has_backup:
        return (
            "ℹ️ <b>Clone Status: INACTIVE (Backup Available)</b>\n\n"
            f"• <b>Original Name:</b> <code>{html.escape(backup.get('first_name', ''))} {html.escape(backup.get('last_name', ''))}</code>\n"
            f"• <b>Original DP:</b> {'Yes' if backup.get('has_dp') else 'No'}\n\n"
            "💡 Use <code>.revert</code> to re-apply original profile if needed."
        )
    else:
        return (
            "ℹ️ <b>Clone Status: INACTIVE</b>\n\n"
            "• Koi profile clone active nahi hai.\n"
            "• Use <code>.clone [reply / @username / id]</code> to clone a user."
        )
