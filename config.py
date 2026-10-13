import os
import re
import json
import base64
import struct
import ipaddress
import logging
from logging.handlers import RotatingFileHandler
import threading
import contextvars
from dotenv import load_dotenv

_LOG_LOCK = threading.Lock()
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

load_dotenv()

def _safe_int(val, default: int = 0) -> int:
    try:
        return int(str(val).strip()) if val is not None and str(val).strip() else default
    except (ValueError, TypeError):
        return default

SANGMATA_API_ID = _safe_int(os.getenv("SANGMATA_API_ID"), 6)
SANGMATA_API_HASH = (os.getenv("SANGMATA_API_HASH") or "").strip() or "eb06d4abfb49dc3eeb1aeb98ae0f581e"
SANGMATA_SESSION = (os.getenv("SANGMATA_SESSION") or "").strip()

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
OWNER_ID = _safe_int(os.getenv("OWNER_ID"), 0)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.mistral.ai/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "pixtral-12b-2409")
VOTES = _safe_int(os.getenv("VOTES"), 3)
if not (1 <= VOTES <= 7):
    logging.warning("VOTES=%s out of range [1-7]; defaulting to 3", VOTES)
    VOTES = 3

APINEX_BASE_URL = os.getenv("APINEX_BASE_URL", "https://api.apinex.bond/v1").rstrip("/")
GPT_MODEL = os.getenv("GPT_MODEL", "free/gpt-6-luna")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_BASE_URL = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1").rstrip("/")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "pixtral-12b-2409")

GAME_BOT = os.getenv("GAME_BOT", "hiddenwordhuntbot")
TRIGGER = os.getenv("TRIGGER", r"^(\.k|\.c)$")
UPSTREAM_REPO = os.getenv("UPSTREAM_REPO", "https://github.com/Viperr-userbot/Viper-Userbot.git").strip() or "https://github.com/Viperr-userbot/Viper-Userbot.git"
UPSTREAM_BRANCH = os.getenv("UPSTREAM_BRANCH", "main").strip() or "main"

_groq_keys_env = os.getenv("GROQ_API_KEYS", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEY_BACKUP = os.getenv("GROQ_API_KEY_BACKUP", "")
if _groq_keys_env:
    GROQ_API_KEYS = [k.strip() for k in _groq_keys_env.split(",") if k.strip()]
else:
    GROQ_API_KEYS = [k.strip() for k in [GROQ_API_KEY, GROQ_API_KEY_BACKUP] if k and k.strip()]
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
GROQ_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_FREE_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "liquid/lfm-2.5-2.6b:free",
    "openrouter/free",
    "google/gemma-4-31b-it:free",
]

_gemini_keys_env = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEYS = [k.strip() for k in _gemini_keys_env.split(",") if k.strip()]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")

SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY", "")
SAMBANOVA_BASE_URL = os.getenv("SAMBANOVA_BASE_URL", "https://api.sambanova.ai/v1").rstrip("/")
SAMBANOVA_MODEL = os.getenv("SAMBANOVA_MODEL", "MiniMax-M3")
SAMBANOVA_MODELS = [
    "MiniMax-M3",
    "gemma-4-31B-it",
]

MISTRAL_API_KEY_BACKUP = os.getenv("MISTRAL_API_KEY_BACKUP", "")
MISTRAL_API_KEYS = [k.strip() for k in [MISTRAL_API_KEY, MISTRAL_API_KEY_BACKUP] if k and k.strip()]
MISTRAL_TEXT_MODELS = [
    "ministral-8b-latest",
    "pixtral-12b-2409",
    "open-mistral-nemo",
    "open-mistral-7b",
]

CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")

OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "").strip()

POLLINATIONS_URL = os.getenv("POLLINATIONS_URL", "https://text.pollinations.ai/openai").rstrip("/")
POLLINATIONS_MODEL = os.getenv("POLLINATIONS_MODEL", "openai")

OPENAGENTIC_BASE_URL = os.getenv("OPENAGENTIC_BASE_URL", "https://openagentic.id/api/v1").rstrip("/")
OPENAGENTIC_API_KEY = os.getenv("OPENAGENTIC_API_KEY", "")
_oa_keys_str = os.getenv("OPENAGENTIC_API_KEYS", "") or OPENAGENTIC_API_KEY
OPENAGENTIC_API_KEYS = [k.strip() for k in _oa_keys_str.split(",") if k.strip()]

OPENAGENTIC_FREE_MODELS = [
    "big-pickle",
    "deepseek-v4.1-flash-free",
    "claude-haiku-5.5-free",
    "mimo-v2.6-flash",
    "muse-spark-1.3-free",
    "space-bunny-free",
]

OPENAGENTIC_VISION_MODELS = [
    "claude-haiku-5.5-free",
    "big-pickle",
]

OPENAGENTIC_IMAGE_MODEL = "ali-z-image-turbo"

_api_keys_str = os.getenv("APINEX_API_KEYS", "")
if _api_keys_str:
    APINEX_API_KEYS = [k.strip() for k in _api_keys_str.split(",") if k.strip()]
else:
    APINEX_API_KEYS = []

import time

_current_key_idx = 0
_exhausted_keys: dict[str, float] = {}

def report_key_error(key: str, status_code: int = 0):
    """Delegate to core.keypool for centralized quarantine and cooldown."""
    try:
        from core.keypool import mark_key_failed
        mark_key_failed(key, status_code)
    except Exception:

        now = time.time()
        cooldown = 86400 if status_code in (401, 402) else (25 if status_code == 429 else 20)
        _exhausted_keys[key] = now + cooldown

def report_key_success(key: str):
    """Delegate to core.keypool for centralized active key rotation."""
    try:
        from core.keypool import mark_key_ok
        mark_key_ok(key)
    except Exception:
        _exhausted_keys.pop(key, None)

import random

def get_ordered_apinex_keys(only_healthy: bool = False) -> list[str]:
    """
    Returns Apinex keys with healthy keys randomly shuffled for every request:
    - Distributes load dynamically across all 6-7 available API keys so no single key
      hits rate limits and total throughput is maximized.
    - If a chosen key fails or hits 429, the remaining shuffled keys act as immediate fallbacks.
    """
    if not APINEX_API_KEYS:
        return []
    now = time.time()
    healthy = [k for k in APINEX_API_KEYS if _exhausted_keys.get(k, 0) <= now]
    random.shuffle(healthy)
    if only_healthy:
        return healthy
    exhausted = sorted([k for k in APINEX_API_KEYS if _exhausted_keys.get(k, 0) > now],
                       key=lambda k: _exhausted_keys.get(k, 0))
    return healthy + exhausted

def has_healthy_apinex_keys() -> bool:
    """Returns True if there is at least one healthy Apinex key available right now."""
    return len(get_ordered_apinex_keys(only_healthy=True)) > 0

def get_active_apinex_key() -> str:
    """
    Returns the current best Apinex API key.
    Keeps using the same key as long as it works so tokens on other keys are preserved.
    """
    global _current_key_idx
    ordered = get_ordered_apinex_keys()
    if not ordered:
        raise ValueError("No APINEX_API_KEYS found in environment.")
    active = ordered[0]
    if active in APINEX_API_KEYS:
        _current_key_idx = APINEX_API_KEYS.index(active)
    return active

def switch_to_next_apinex_key(failed_key: str | None = None, status_code: int = 0) -> str:
    """
    Marks the given key as failed and immediately selects the next healthy key.
    """
    global _current_key_idx
    if failed_key:
        report_key_error(failed_key, status_code)
    elif APINEX_API_KEYS:
        _current_key_idx = (_current_key_idx + 1) % len(APINEX_API_KEYS)
    return get_active_apinex_key()

def get_next_apinex_key() -> str:
    """Returns next healthy Apinex key."""
    return switch_to_next_apinex_key()

def get_healthy_keys(count: int = 1) -> list[str]:
    """Returns up to 'count' distinct healthy Apinex keys."""
    ordered = get_ordered_apinex_keys()
    if not ordered:
        raise ValueError("No APINEX_API_KEYS found in environment.")
    return [ordered[i % len(ordered)] for i in range(count)]

def get_telethon_session():
    """Parses and returns a valid Telethon session (supporting Telethon, Pyrogram, and Pascal-string formats)."""
    if not SANGMATA_SESSION:
        return os.getenv("SESSION_NAME", "hwh_userbot")

    from telethon.sessions import StringSession
    s = SANGMATA_SESSION.strip()

    if s.startswith("BQ"):
        try:
            data = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
            DCs = {
                1: ("149.154.175.50", 443),
                2: ("149.154.167.51", 443),
                3: ("149.154.175.100", 443),
                4: ("149.154.167.91", 443),
                5: ("91.108.56.165", 443)
            }
            dc_id, api_id, test_mode, auth_key, user_id, is_bot = struct.unpack(">B I ? 256s Q ?", data)
            ip_str, port = DCs.get(dc_id, ("149.154.167.51", 443))
            ip_packed = ipaddress.ip_address(ip_str).packed
            telethon_raw = struct.pack(">B4sH256s", dc_id, ip_packed, port, auth_key)
            telethon_str = "1" + base64.urlsafe_b64encode(telethon_raw).decode()
            return StringSession(telethon_str)
        except Exception:
            pass

    try:
        return StringSession(s)
    except Exception:

        version = s[0]
        raw = base64.b64decode(s[1:])
        dc_id = raw[0]
        str_len = struct.unpack(">H", raw[1:3])[0]
        ip_str = raw[3:3+str_len].decode()
        port = struct.unpack(">H", raw[3+str_len:3+str_len+2])[0]
        auth_key = raw[3+str_len+2:]

        ip_packed = ipaddress.ip_address(ip_str).packed
        telethon_raw = struct.pack(">B4sH256s", dc_id, ip_packed, port, auth_key)
        normalized = version + base64.urlsafe_b64encode(telethon_raw).decode()
        return StringSession(normalized)

_LOG_LOCK = __import__("threading").RLock()

def validate_config() -> None:
    """Fail-fast, human-readable config validation at startup."""
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN is not set in .env")
    if not APINEX_API_KEYS:
        logging.getLogger("hwh").warning("APINEX_API_KEYS is empty in .env; falling back to alternative providers or Pollinations.")
    if VOTES < 1 or VOTES > 7:
        errors.append("VOTES=%s is out of range [1, 7]; defaulting to 3" % VOTES)
        globals()["VOTES"] = 3
    if APINEX_API_KEYS and not has_healthy_apinex_keys():
        logging.getLogger("hwh").warning("No healthy Apinex keys currently available; requests will fall back to secondary providers.")
    if errors:
        sec_log = logging.getLogger("hwh.security")
        sec_log.error(json.dumps({
            "event": "config_validation_failed",
            "reason": "; ".join(errors)
        }))
        for e in errors:
            print("CONFIG ERROR: %s" % e)
        raise ValueError("; ".join(errors))

class RequestIdFilter(logging.Filter):
    """Injects current request ID from contextvars into each LogRecord."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.req_id = request_id_var.get()
        return True

class SecretRedactionFilter(logging.Filter):
    """Redacts bot tokens, API keys, Bearer auth, and Telegram session strings from logs."""
    _PATTERNS = [
        re.compile(r"\d{8,11}:[A-Za-z0-9_-]{35}"),
        re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{15,}", re.IGNORECASE),
        re.compile(r"(?:sk|gsk)(?:-[A-Za-z0-9_-]{15,}|_[A-Za-z0-9_-]{15,})"),
        re.compile(r"\b(?:BQ[A-Za-z0-9_-]{40,}|1Ag[A-Za-z0-9_-]{40,})\b"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pat in self._PATTERNS:
                record.msg = pat.sub("***REDACTED***", record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (self._redact_str(v) if isinstance(v, str) else v) for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple((self._redact_str(v) if isinstance(v, str) else v) for v in record.args)
        return True

    @classmethod
    def _redact_str(cls, val: str) -> str:
        for pat in cls._PATTERNS:
            val = pat.sub("***REDACTED***", val)
        return val

class JsonFormatter(logging.Formatter):
    """Outputs exactly one valid JSON line per log event, escaping all message content."""
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "level": record.levelname,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "logger": record.name,
            "req_id": getattr(record, "req_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(data)

def setup_logging(level=None, json_format=True, log_dir=None):
    log_dir = log_dir or os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    level = logging.getLevelName((level or os.getenv("LOG_LEVEL", "INFO")).upper())
    formatter = JsonFormatter() if json_format else logging.Formatter("[%(req_id)s] %(asctime)s [%(levelname)s] %(name)s: %(message)s")
    rhandler = RotatingFileHandler(
        os.path.join(log_dir, "hwh.log"), maxBytes=5 * 1024 * 1024, backupCount=5
    )
    rhandler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    redaction = SecretRedactionFilter()
    req_filter = RequestIdFilter()
    rhandler.addFilter(req_filter)
    rhandler.addFilter(redaction)
    console.addFilter(req_filter)
    console.addFilter(redaction)

    log = logging.getLogger("hwh")
    log.setLevel(level)
    log.addFilter(req_filter)
    log.addFilter(redaction)
    with _LOG_LOCK:
        log.handlers = []
        log.addHandler(rhandler)
        log.addHandler(console)
    logging.getLogger().addFilter(req_filter)
    logging.getLogger().addFilter(redaction)
    return log

_raw_sig = os.getenv("WEBHOOK_SIGNATURE_TOKEN")
if not _raw_sig:
    import hashlib
    _raw_sig = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()
WEBHOOK_SIGNATURE_TOKEN = _raw_sig

