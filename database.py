import os
import json
import logging

log = logging.getLogger("hwh.database")

def _get_owner_id() -> int:
    try:
        import config
        return int(getattr(config, "OWNER_ID", 0) or 0)
    except Exception:
        val = os.getenv("OWNER_ID", "")
        return int(val.strip()) if val and val.strip().isdigit() else 0

PROMOTED_DB_FILE = "promoted_users.json"
SUDO_DB_FILE = "sudo_users.json"
AUTH_GCS_DB_FILE = "auth_gcs.json"
DATABASE_URL = os.getenv("DATABASE_URL", "")

_promoted_cache: set[int] | None = None
_sudo_cache: set[int] | None = None
_auth_gcs_cache: dict[int, str] | None = None

def _get_connection():
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = True
        return conn
    except Exception as e:
        log.error(f"Failed to connect to PostgreSQL: {e}")
        return None

if DATABASE_URL:
    try:
        conn = _get_connection()
        if conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS promoted_users (
                        user_id BIGINT PRIMARY KEY
                    );
                    CREATE TABLE IF NOT EXISTS sudo_users (
                        user_id BIGINT PRIMARY KEY
                    );
                    CREATE TABLE IF NOT EXISTS auth_gcs (
                        chat_id BIGINT PRIMARY KEY,
                        title TEXT DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS bot_memories (
                        id SERIAL PRIMARY KEY,
                        content TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS bot_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    );
                """)
            conn.close()
            log.info("PostgreSQL tables (promoted_users, sudo_users, auth_gcs, bot_memories, bot_settings) initialized successfully.")
    except Exception as e:
        log.error(f"Failed to initialize PostgreSQL tables: {e}")

def get_promoted_users() -> set[int]:
    global _promoted_cache
    if _promoted_cache is not None:
        return _promoted_cache.copy()

    users = set()
    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT user_id FROM promoted_users")
                    rows = cur.fetchall()
                    users = set(int(row[0]) for row in rows)
            except Exception as e:
                log.error(f"PostgreSQL read error (promoted_users): {e}")
            finally:
                conn.close()

    if not users and os.path.exists(PROMOTED_DB_FILE):
        try:
            with open(PROMOTED_DB_FILE, "r") as f:
                data = json.load(f)
                users = set(int(u) for u in data.get("users", []))
        except Exception as e:
            log.error(f"JSON read error ({PROMOTED_DB_FILE}): {e}")

    _promoted_cache = users
    return _promoted_cache.copy()

def _invalidate_auth_cache():
    """Clear core.auth 2s membership cache so sudo/promoted changes apply instantly.
    Lazy import avoids circular dependency (core.auth imports database)."""
    try:
        from core.auth import clear_auth_cache
        clear_auth_cache()
    except Exception:
        pass

def add_promoted_user(user_id: int):
    _invalidate_auth_cache()
    global _promoted_cache
    user_id = int(user_id)
    if _promoted_cache is not None:
        _promoted_cache.add(user_id)
    else:
        _promoted_cache = get_promoted_users()
        _promoted_cache.add(user_id)

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO promoted_users (user_id) VALUES (%s)
                        ON CONFLICT (user_id) DO NOTHING
                    """, (user_id,))
            except Exception as e:
                log.error(f"PostgreSQL write error (promoted_users): {e}")
            finally:
                conn.close()

    try:
        with open(PROMOTED_DB_FILE, "w") as f:
            json.dump({"users": list(_promoted_cache)}, f)
    except Exception as e:
        log.error(f"Failed to write {PROMOTED_DB_FILE}: {e}")

def remove_promoted_user(user_id: int):
    _invalidate_auth_cache()
    global _promoted_cache
    user_id = int(user_id)
    if _promoted_cache is not None:
        _promoted_cache.discard(user_id)
    else:
        _promoted_cache = get_promoted_users()
        _promoted_cache.discard(user_id)

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM promoted_users WHERE user_id = %s", (user_id,))
            except Exception as e:
                log.error(f"PostgreSQL delete error (promoted_users): {e}")
            finally:
                conn.close()

    try:
        with open(PROMOTED_DB_FILE, "w") as f:
            json.dump({"users": list(_promoted_cache)}, f)
    except Exception as e:
        log.error(f"Failed to write {PROMOTED_DB_FILE}: {e}")

def is_promoted_user(user_id: int) -> bool:
    return int(user_id) in get_promoted_users()

def get_sudo_users() -> set[int]:
    global _sudo_cache
    if _sudo_cache is not None:
        return _sudo_cache.copy()

    users = set()
    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT user_id FROM sudo_users")
                    rows = cur.fetchall()
                    users = set(int(row[0]) for row in rows)
            except Exception as e:
                log.error(f"PostgreSQL read error (sudo_users): {e}")
            finally:
                conn.close()

    if not users and os.path.exists(SUDO_DB_FILE):
        try:
            with open(SUDO_DB_FILE, "r") as f:
                data = json.load(f)
                users = set(int(u) for u in data.get("users", []))
        except Exception as e:
            log.error(f"JSON read error ({SUDO_DB_FILE}): {e}")

    _sudo_cache = users
    return _sudo_cache.copy()

def add_sudo_user(user_id: int):
    _invalidate_auth_cache()
    global _sudo_cache
    user_id = int(user_id)
    if _sudo_cache is not None:
        _sudo_cache.add(user_id)
    else:
        _sudo_cache = get_sudo_users()
        _sudo_cache.add(user_id)

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO sudo_users (user_id) VALUES (%s)
                        ON CONFLICT (user_id) DO NOTHING
                    """, (user_id,))
            except Exception as e:
                log.error(f"PostgreSQL write error (sudo_users): {e}")
            finally:
                conn.close()

    try:
        with open(SUDO_DB_FILE, "w") as f:
            json.dump({"users": list(_sudo_cache)}, f)
    except Exception as e:
        log.error(f"Failed to write {SUDO_DB_FILE}: {e}")

def remove_sudo_user(user_id: int):
    _invalidate_auth_cache()
    global _sudo_cache
    user_id = int(user_id)
    if _sudo_cache is not None:
        _sudo_cache.discard(user_id)
    else:
        _sudo_cache = get_sudo_users()
        _sudo_cache.discard(user_id)

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM sudo_users WHERE user_id = %s", (user_id,))
            except Exception as e:
                log.error(f"PostgreSQL delete error (sudo_users): {e}")
            finally:
                conn.close()

    try:
        with open(SUDO_DB_FILE, "w") as f:
            json.dump({"users": list(_sudo_cache)}, f)
    except Exception as e:
        log.error(f"Failed to write {SUDO_DB_FILE}: {e}")

def is_sudo_user(user_id: int) -> bool:
    try:
        return int(user_id) in get_sudo_users()
    except Exception:
        return False

_auth_gcs_cache: dict[int, str] | None = None
_auth_gc_users_cache: dict[int, set[int]] | None = None

def _load_auth_gcs():
    global _auth_gcs_cache, _auth_gc_users_cache
    gcs: dict[int, str] = {}
    gc_users: dict[int, set[int]] = {}

    oid = _get_owner_id()
    owner_fallback = {oid} if oid else set()

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute("ALTER TABLE auth_gcs ADD COLUMN IF NOT EXISTS auth_users TEXT DEFAULT '';")
                        conn.commit()
                    except Exception:
                        pass
                    cur.execute("SELECT chat_id, title, auth_users FROM auth_gcs")
                    rows = cur.fetchall()
                    for row in rows:
                        cid = int(row[0])
                        title = str(row[1] or "")
                        gcs[cid] = title
                        raw_u = str(row[2] or "").strip()
                        if raw_u:
                            try:
                                u_set = set(int(x) for x in raw_u.split(",") if x.strip())
                            except Exception:
                                u_set = set(owner_fallback)
                        else:
                            u_set = set(owner_fallback)
                        gc_users[cid] = u_set
            except Exception as e:
                log.error(f"PostgreSQL read error (auth_gcs): {e}")
            finally:
                conn.close()

    if not gcs and os.path.exists(AUTH_GCS_DB_FILE):
        try:
            with open(AUTH_GCS_DB_FILE, "r") as f:
                data = json.load(f)
                raw = data.get("gcs", {})
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        try:
                            cid = int(k)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(v, dict):
                            gcs[cid] = str(v.get("title", ""))
                            u_list = v.get("users", [])
                            gc_users[cid] = set(int(u) for u in u_list) if u_list else set(owner_fallback)
                        else:
                            gcs[cid] = str(v)
                            gc_users[cid] = set(owner_fallback)
                elif isinstance(raw, list):
                    for k in raw:
                        try:
                            cid = int(k)
                            gcs[cid] = ""
                            gc_users[cid] = set(owner_fallback)
                        except (ValueError, TypeError):
                            continue
        except Exception as e:
            log.error(f"JSON read error ({AUTH_GCS_DB_FILE}): {e}")

    _auth_gcs_cache = gcs
    _auth_gc_users_cache = gc_users

def _save_auth_gcs():
    if _auth_gcs_cache is None:
        return

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    for cid, title in _auth_gcs_cache.items():
                        users_str = ",".join(str(u) for u in _auth_gc_users_cache.get(cid, set()))
                        cur.execute("""
                            INSERT INTO auth_gcs (chat_id, title, auth_users) VALUES (%s, %s, %s)
                            ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title, auth_users = EXCLUDED.auth_users
                        """, (cid, title, users_str))
                conn.commit()
            except Exception as e:
                log.error(f"PostgreSQL write error (auth_gcs): {e}")
            finally:
                conn.close()

    try:
        data = {}
        for cid, title in _auth_gcs_cache.items():
            u_list = list(_auth_gc_users_cache.get(cid, set()))
            data[str(cid)] = {"title": title, "users": u_list}
        with open(AUTH_GCS_DB_FILE, "w") as f:
            json.dump({"gcs": data}, f)
    except Exception as e:
        log.error(f"Failed to write {AUTH_GCS_DB_FILE}: {e}")

def get_auth_gcs() -> dict[int, str]:
    if _auth_gcs_cache is None:
        _load_auth_gcs()
    return _auth_gcs_cache.copy()

def get_auth_gc_ids() -> set[int]:
    return set(get_auth_gcs().keys())

def get_gc_auth_users(chat_id: int) -> set[int]:
    if _auth_gc_users_cache is None:
        _load_auth_gcs()
    return _auth_gc_users_cache.get(int(chat_id), set()).copy()

def is_auth_gc(chat_id: int | None) -> bool:
    if not chat_id:
        return False
    try:
        return int(chat_id) in get_auth_gcs()
    except Exception:
        return False

def is_auth_gc_for_user(chat_id: int | None, user_id: int | None) -> bool:
    if not chat_id:
        return False
    cid = int(chat_id)
    if not is_auth_gc(cid):
        return False
    if user_id is None:
        return True
    uid = int(user_id)
    owner_id = _get_owner_id()
    if owner_id and uid == owner_id:
        return True
    users = get_gc_auth_users(cid)
    if not users:
        return True
    return uid in users

def add_auth_gc_user(chat_id: int, user_id: int, title: str = ""):
    _invalidate_auth_cache()
    if _auth_gcs_cache is None or _auth_gc_users_cache is None:
        _load_auth_gcs()
    cid = int(chat_id)
    uid = int(user_id)
    if title:
        _auth_gcs_cache[cid] = str(title)
    elif cid not in _auth_gcs_cache:
        _auth_gcs_cache[cid] = ""

    if cid not in _auth_gc_users_cache:
        _auth_gc_users_cache[cid] = set()
    _auth_gc_users_cache[cid].add(uid)
    _save_auth_gcs()

def remove_auth_gc_user(chat_id: int, user_id: int):
    _invalidate_auth_cache()
    if _auth_gcs_cache is None or _auth_gc_users_cache is None:
        _load_auth_gcs()
    cid = int(chat_id)
    uid = int(user_id)
    if cid in _auth_gc_users_cache:
        _auth_gc_users_cache[cid].discard(uid)
        if not _auth_gc_users_cache[cid]:
            _auth_gcs_cache.pop(cid, None)
            _auth_gc_users_cache.pop(cid, None)
            if DATABASE_URL:
                conn = _get_connection()
                if conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute("DELETE FROM auth_gcs WHERE chat_id = %s", (cid,))
                        conn.commit()
                    finally:
                        conn.close()
    _save_auth_gcs()

def add_auth_gc(chat_id: int, title: str = "", user_id: int | None = None):
    uid = int(user_id) if user_id else _get_owner_id()
    add_auth_gc_user(chat_id, uid, title)

def remove_auth_gc(chat_id: int, user_id: int | None = None):
    if user_id is not None:
        remove_auth_gc_user(chat_id, int(user_id))
    else:
        _invalidate_auth_cache()
        if _auth_gcs_cache is None or _auth_gc_users_cache is None:
            _load_auth_gcs()
        cid = int(chat_id)
        _auth_gcs_cache.pop(cid, None)
        _auth_gc_users_cache.pop(cid, None)
        if DATABASE_URL:
            conn = _get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM auth_gcs WHERE chat_id = %s", (cid,))
                    conn.commit()
                finally:
                    conn.close()
        _save_auth_gcs()

MEMORIES_DB_FILE = "bot_memories.json"
_memories_cache: list[dict] | None = None

def get_memories() -> list[dict]:
    global _memories_cache
    if _memories_cache is not None:
        return [dict(m) for m in _memories_cache]

    memories = []
    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, content FROM bot_memories ORDER BY id ASC")
                    rows = cur.fetchall()
                    memories = [{"id": int(r[0]), "content": str(r[1])} for r in rows]
            except Exception as e:
                log.error(f"PostgreSQL read error (bot_memories): {e}")
            finally:
                conn.close()

    if not memories and os.path.exists(MEMORIES_DB_FILE):
        try:
            with open(MEMORIES_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                memories = data.get("memories", [])
        except Exception as e:
            log.error(f"JSON read error ({MEMORIES_DB_FILE}): {e}")

    _memories_cache = memories
    return [dict(m) for m in _memories_cache]

def add_memory(content: str) -> dict:
    global _memories_cache
    content = content.strip()
    memories = get_memories()
    new_id = (max([m["id"] for m in memories], default=0) + 1)

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO bot_memories (content) VALUES (%s) RETURNING id", (content,))
                    row = cur.fetchone()
                    if row:
                        new_id = int(row[0])
            except Exception as e:
                log.error(f"PostgreSQL write error (bot_memories): {e}")
            finally:
                conn.close()

    mem_item = {"id": new_id, "content": content}
    _memories_cache = [m for m in memories if m["id"] != new_id] + [mem_item]

    try:
        with open(MEMORIES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump({"memories": _memories_cache}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Failed to write {MEMORIES_DB_FILE}: {e}")

    return mem_item

def delete_memory(query: str | int) -> tuple[bool, str]:
    global _memories_cache
    memories = get_memories()
    if not memories:
        return False, ""

    target_item = None
    target_id = None

    if isinstance(query, int) or (isinstance(query, str) and query.strip().isdigit()):
        target_id = int(query)
        for m in memories:
            if m["id"] == target_id:
                target_item = m
                break
    else:

        q_lower = str(query).strip().lower()
        for m in memories:
            if q_lower in m["content"].lower():
                target_item = m
                target_id = m["id"]
                break

    if not target_item or target_id is None:
        return False, ""

    deleted_text = target_item["content"]

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bot_memories WHERE id = %s", (target_id,))
            except Exception as e:
                log.error(f"PostgreSQL delete error (bot_memories): {e}")
            finally:
                conn.close()

    _memories_cache = [m for m in memories if m["id"] != target_id]

    try:
        with open(MEMORIES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump({"memories": _memories_cache}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Failed to write {MEMORIES_DB_FILE}: {e}")

    return True, deleted_text

def clear_all_memories() -> int:
    global _memories_cache
    memories = get_memories()
    count = len(memories)

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bot_memories")
            except Exception as e:
                log.error(f"PostgreSQL delete error (bot_memories): {e}")
            finally:
                conn.close()

    _memories_cache = []
    try:
        with open(MEMORIES_DB_FILE, "w", encoding="utf-8") as f:
            json.dump({"memories": []}, f)
    except Exception as e:
        log.error(f"Failed to write {MEMORIES_DB_FILE}: {e}")

    return count

SETTINGS_DB_FILE = "bot_settings.json"
_settings_cache: dict[str, str] | None = None

def get_setting(key: str, default: str = "") -> str:
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = {}
        if DATABASE_URL:
            conn = _get_connection()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT key, value FROM bot_settings")
                        rows = cur.fetchall()
                        _settings_cache = {str(r[0]): str(r[1]) for r in rows}
                except Exception as e:
                    log.error(f"PostgreSQL read error (bot_settings): {e}")
                finally:
                    conn.close()

        if not _settings_cache and os.path.exists(SETTINGS_DB_FILE):
            try:
                with open(SETTINGS_DB_FILE, "r") as f:
                    _settings_cache = json.load(f).get("settings", {})
            except Exception as e:
                log.error(f"JSON read error ({SETTINGS_DB_FILE}): {e}")

    return _settings_cache.get(key, default)

def set_setting(key: str, value: str):
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = {}
    _settings_cache[key] = str(value)

    if DATABASE_URL:
        conn = _get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO bot_settings (key, value) VALUES (%s, %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """, (key, str(value)))
            except Exception as e:
                log.error(f"PostgreSQL write error (bot_settings): {e}")
            finally:
                conn.close()

    try:
        with open(SETTINGS_DB_FILE, "w") as f:
            json.dump({"settings": _settings_cache}, f, indent=2)
    except Exception as e:
        log.error(f"Failed to write {SETTINGS_DB_FILE}: {e}")

def is_gpt_thinking_mode(chat_id: int | str) -> bool:
    """Per-chat `.1gpt` mode: True = 'Thinking...' reply then answer as a new message. Default OFF."""
    return get_setting(f"gpt_thinking:{chat_id}", "0") == "1"

def set_gpt_thinking_mode(chat_id: int | str, enabled: bool):
    """Enables or disables `.1gpt` for one chat."""
    set_setting(f"gpt_thinking:{chat_id}", "1" if enabled else "0")

def is_auto_reply_enabled() -> bool:
    """Returns True if bot auto-reply is ON. Default is False (OFF)."""
    return get_setting("auto_reply", "0") == "1"

def set_auto_reply_enabled(enabled: bool):
    """Enables or disables bot auto-reply."""
    set_setting("auto_reply", "1" if enabled else "0")

def is_secret_viewonce_enabled() -> bool:
    """Returns True if secret view-once auto saver is enabled. Default is False (OFF)."""
    return get_setting("secret_viewonce", "0") == "1"

def set_secret_viewonce_enabled(enabled: bool):
    """Enables or disables secret view-once auto saver."""
    set_setting("secret_viewonce", "1" if enabled else "0")

def is_afk() -> bool:
    """Returns True if userbot is currently marked as AFK."""
    return get_setting("afk_enabled", "0") == "1"

def set_afk(enabled: bool, reason: str = "", start_time: float | None = None):
    """Enables or disables AFK status with reason and start timestamp."""
    import time
    set_setting("afk_enabled", "1" if enabled else "0")
    if enabled:
        set_setting("afk_reason", reason or "I am currently away.")
        set_setting("afk_time", str(start_time if start_time is not None else time.time()))
    else:
        set_setting("afk_reason", "")
        set_setting("afk_time", "0")

def get_afk_info() -> tuple[bool, str, float]:
    """Returns (is_afk: bool, reason: str, start_time: float)."""
    enabled = is_afk()
    reason = get_setting("afk_reason", "I am currently away.")
    try:
        start_time = float(get_setting("afk_time", "0"))
    except (ValueError, TypeError):
        start_time = 0.0
    return enabled, reason, start_time

def is_humanize_enabled() -> bool:
    """Returns True if humanize delay is ON (simulates typing delay before answering)."""
    return get_setting("humanize_delay", "0") == "1"

def set_humanize_enabled(enabled: bool):
    """Enables or disables quiz humanizer delay."""
    set_setting("humanize_delay", "1" if enabled else "0")

def is_userbot_gc_reply_enabled() -> bool:
    """Returns True if userbot ID should reply directly in authorized GC."""
    return get_setting("userbot_gc_reply", "0") == "1"

def set_userbot_gc_reply_enabled(enabled: bool):
    """Enables or disables userbot ID direct replying in GC."""
    set_setting("userbot_gc_reply", "1" if enabled else "0")

def set_restart_state(chat_id: int | str, msg_id: int | str = "", note: str = ""):
    """Saves pending restart notification chat, message ID, and optional custom text."""
    set_setting("restart_chat_id", str(chat_id))
    set_setting("restart_msg_id", str(msg_id) if msg_id else "")
    if note:
        set_setting("restart_note", str(note))

def get_and_clear_restart_state() -> tuple[int, int | None] | None:
    """Returns (chat_id, msg_id) if a restart notification is pending, and clears the state."""
    chat_str = get_setting("restart_chat_id", "")
    msg_str = get_setting("restart_msg_id", "")
    if not chat_str:
        return None
    set_setting("restart_chat_id", "")
    set_setting("restart_msg_id", "")
    try:
        chat_id = int(chat_str)
        msg_id = int(msg_str) if msg_str else None
        return chat_id, msg_id
    except (ValueError, TypeError):
        return None

def get_and_clear_restart_note() -> str:
    """Returns optional custom restart notification text, e.g. for update messages."""
    note = get_setting("restart_note", "")
    if note:
        set_setting("restart_note", "")
    return note


# ============================================================================
# CLONE & REVERT PROFILE SYSTEM (.clone, .revert)
# ============================================================================

def is_clone_active() -> bool:
    """Returns True if userbot is currently cloned to another user's profile."""
    return get_setting("clone_active", "0") == "1"


def set_clone_active(active: bool, target_id: int | None = None, target_name: str = ""):
    """Marks profile cloning as active or inactive with target metadata."""
    set_setting("clone_active", "1" if active else "0")
    if active:
        if target_id is not None:
            set_setting("clone_target_id", str(target_id))
        if target_name:
            set_setting("clone_target_name", str(target_name))
    else:
        set_setting("clone_target_id", "")
        set_setting("clone_target_name", "")


def has_clone_backup() -> bool:
    """Returns True if a backup of the original profile exists."""
    return get_setting("clone_has_backup", "0") == "1"


def save_clone_backup(first_name: str, last_name: str, about: str, has_dp: bool):
    """Saves original profile details before cloning."""
    set_setting("clone_has_backup", "1")
    set_setting("clone_backup_first", first_name or "")
    set_setting("clone_backup_last", last_name or "")
    set_setting("clone_backup_about", about or "")
    set_setting("clone_backup_has_dp", "1" if has_dp else "0")


def get_clone_backup() -> dict:
    """Returns a dict containing backed up original profile details."""
    return {
        "has_backup": has_clone_backup(),
        "first_name": get_setting("clone_backup_first", ""),
        "last_name": get_setting("clone_backup_last", ""),
        "about": get_setting("clone_backup_about", ""),
        "has_dp": get_setting("clone_backup_has_dp", "0") == "1",
        "target_id": get_setting("clone_target_id", ""),
        "target_name": get_setting("clone_target_name", ""),
    }


def clear_clone_backup():
    """Clears original profile backup metadata."""
    set_setting("clone_has_backup", "0")
    set_setting("clone_backup_first", "")
    set_setting("clone_backup_last", "")
    set_setting("clone_backup_about", "")
    set_setting("clone_backup_has_dp", "0")
    set_setting("clone_target_id", "")
    set_setting("clone_target_name", "")


