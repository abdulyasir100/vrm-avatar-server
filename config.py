"""Avatar Server Configuration

Reads from .env file if present, falls back to defaults.
Secrets and network IPs should be set in .env (see .env.example).
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # ENV_FILE points a second instance at its own .env (same checkout, other character).
    load_dotenv(os.environ.get("ENV_FILE") or None)
except ImportError:
    pass

def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)

def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes")

def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    return float(val)

def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    return int(val)

def _env_list(key: str, default: str) -> list[str]:
    """Comma-separated env var -> list, blanks dropped."""
    return [v.strip() for v in os.environ.get(key, default).split(",") if v.strip()]

# --- Server ---
AVATAR_SERVER_HOST = _env("AVATAR_SERVER_HOST", "0.0.0.0")
AVATAR_SERVER_PORT = _env_int("AVATAR_SERVER_PORT", 8800)

# --- Instance identity & paths ---
# One engine, many characters: everything an instance writes derives from these
# roots. Defaults are the historic relative paths, so a lone instance needs none of it.
def _under(root: str, *parts: str) -> str:
    return Path(root, *parts).as_posix()

CHARACTER_ID = _env("CHARACTER_ID", "")  # slug for logs / compose suffix; "" = the only instance
DATA_DIR = _env("DATA_DIR", "data")
AUDIO_DIR = _env("AUDIO_DIR", "audio")
LOG_DIR = _env("LOG_DIR", "logs")
PLUGIN_DATA_DIR = _env("PLUGIN_DATA_DIR", _under(DATA_DIR, "plugins"))
RUNTIME_CONFIG_PATH = _env("RUNTIME_CONFIG_PATH", _under(DATA_DIR, "runtime_config.json"))
USER_STATE_PATH = _env("USER_STATE_PATH", _under(DATA_DIR, "user_state.json"))
BACKGROUND_DAILY_STATE_PATH = _env("BACKGROUND_DAILY_STATE_PATH", _under(DATA_DIR, "background_daily.json"))
COSTUMES_PATH = _env("COSTUMES_PATH", _under(DATA_DIR, "costumes.json"))
SQLITE_WAL = _env_bool("SQLITE_WAL", True)

# --- Second brain (services/brain): facts about the owner, shared across characters ---
# sqlite = a local file, all a lone instance needs. postgres = one brain for several
# harnesses; BRAIN_DSN carries a password, so it is env-only (never a settings row).
BRAIN_BACKEND = _env("BRAIN_BACKEND", "sqlite")
BRAIN_DB_PATH = _env("BRAIN_DB_PATH", _under(DATA_DIR, "brain.db"))
BRAIN_DSN = _env("BRAIN_DSN", "")
BRAIN_PROMPT_MAX = _env_int("BRAIN_PROMPT_MAX", 150)  # shared facts per prompt before recall kicks in
# Plugin allowlist on top of each manifest's `enabled`: "" = all, "none" = none, else csv.
PLUGINS_ENABLED = _env("PLUGINS_ENABLED", "")


def plugin_allowed(name: str) -> bool:
    spec = PLUGINS_ENABLED.strip().lower()
    if not spec:
        return True
    if spec == "none":
        return False
    return name.lower() in {p.strip() for p in spec.split(",")}

# --- LLM Provider ---
LLM_PROVIDER = _env("LLM_PROVIDER", "lmstudio")
LLM_BASE_URL = _env("LLM_BASE_URL", "") or _env("LM_STUDIO_BASE_URL", "")  # legacy compat
LLM_MODEL = _env("LLM_MODEL", "") or _env("LM_STUDIO_MODEL", "")  # legacy compat
LLM_API_KEY = _env("LLM_API_KEY", "")
LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 20)

# --- LLM Fallback Chain ---
LLM_FALLBACK_1 = _env("LLM_FALLBACK_1", "")  # e.g. "groq"
LLM_FALLBACK_1_API_KEY = _env("LLM_FALLBACK_1_API_KEY", "")
LLM_FALLBACK_2 = _env("LLM_FALLBACK_2", "")  # e.g. "mistral"
LLM_FALLBACK_2_API_KEY = _env("LLM_FALLBACK_2_API_KEY", "")

_PROVIDER_DEFAULTS = {
    "lmstudio": {
        "base_url": "http://localhost:1235/v1",
        "api_key": "lm-studio",
        "model": "darkidol-llama-3.1-8b-instruct-1.2-uncensored",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "llama3.1:8b",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "",
        "model": "openai/gpt-oss-120b",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key": "",
        "model": "mistral-medium-latest",
    },
    "claude": {
        "base_url": "",
        "api_key": "",
        "model": "sonnet",
    },
}

def get_llm_base_url() -> str:
    return LLM_BASE_URL or _PROVIDER_DEFAULTS.get(LLM_PROVIDER, {}).get("base_url", "http://localhost:1235/v1")

def get_llm_api_key() -> str:
    return LLM_API_KEY or _PROVIDER_DEFAULTS.get(LLM_PROVIDER, {}).get("api_key", "")

def get_llm_model() -> str:
    return LLM_MODEL or _PROVIDER_DEFAULTS.get(LLM_PROVIDER, {}).get("model", "")


def get_llm_fallback_chain() -> list[dict]:
    """Return list of fallback provider configs [{provider, base_url, api_key, model}, ...]."""
    chain = []
    for fb_provider, fb_key in [(LLM_FALLBACK_1, LLM_FALLBACK_1_API_KEY),
                                 (LLM_FALLBACK_2, LLM_FALLBACK_2_API_KEY)]:
        if not fb_provider:
            continue
        defaults = _PROVIDER_DEFAULTS.get(fb_provider)
        if not defaults:  # unknown/retired provider — skip rather than call an empty URL
            continue
        chain.append({
            "provider": fb_provider,
            "base_url": defaults.get("base_url", ""),
            "api_key": fb_key or defaults.get("api_key", ""),
            "model": defaults.get("model", ""),
        })
    return chain

# --- System Prompt / Character Card ---
SYSTEM_PROMPT_PATH = _env("SYSTEM_PROMPT_PATH", "prompts/system.md")
CHARACTER_CARD_PATH = _env("CHARACTER_CARD_PATH", "character.json")
PERSONALITY_PROMPT_PATH = _env("PERSONALITY_PROMPT_PATH", "prompts/personality.md")
OWNER_PROMPT_PATH = _env("OWNER_PROMPT_PATH", "prompts/owner.md")
TOOL_DOCS_PATH = _env("TOOL_DOCS_PATH", "prompts/tools")
# Fills {{OWNER_NAME}} in prompt files; the card's owner.name applies when the env is unset.
OWNER_NAME = _env("OWNER_NAME", "the user")
# Fills {{OWNER_PRONOUN}}; the card's owner.pronoun applies when the env is unset.
OWNER_PRONOUN = _env("OWNER_PRONOUN", "they")

# --- Locale / Timezone ---
# TIMEZONE is an IANA name (e.g. "Asia/Jakarta", "America/New_York"). When set it
# takes precedence; otherwise a fixed UTC offset from TZ_OFFSET_HOURS is used.
# TZ_LABEL is a short display label appended to times (e.g. "WIB", "EST"); optional.
# All of these can also be provided in character.json (env wins if both are set).
TIMEZONE = _env("TIMEZONE", "")
TZ_OFFSET_HOURS = _env_float("TZ_OFFSET_HOURS", 0.0)
TZ_LABEL = _env("TZ_LABEL", "")
# Env var is CHARACTER_LANGUAGE (LANGUAGE clashes with the standard gettext env
# var); the config attribute stays LANGUAGE, and the character.json key stays
# "language".
LANGUAGE = _env("CHARACTER_LANGUAGE", "en")
LOCALE_COUNTRY = _env("LOCALE_COUNTRY", "")

# Human-readable names for {{LANGUAGE}} interpolation and the mode system prompts.
# Unknown codes render as themselves.
_LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "id": "Indonesian",
    "zh": "Chinese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}


def get_language_name(code: str | None = None) -> str:
    """Human-readable name for a language code (default: the configured LANGUAGE)."""
    code = code if code is not None else LANGUAGE
    code = (code or "").strip()
    return _LANGUAGE_NAMES.get(code.lower(), code)

# --- Emotion Tags ---
VALID_EMOTIONS = ["HAPPY", "SAD", "SURPRISED", "ANGRY", "THINKING", "NEUTRAL"]
DEFAULT_EMOTION = "NEUTRAL"

# --- Tool Call Sensitivity ---
# normal       — all tools callable from both user messages and idle/background
# semi_normal  — only main tools (costume, memory, gacha, etc.) from user; plugin tools blocked; idle unrestricted
# semi_off     — no tool calls from user messages; idle/background can still call tools
# off          — no tool calls anywhere (user + idle)
TOOL_CALL_MODE = _env("TOOL_CALL_MODE", "normal")

# --- Plugin Intent Prefix ---
# When True, plugin tools only become visible if the message starts with a
# character nickname ("suichan, ..."). When False (default), the model decides
# when to use a plugin tool on its own — full-trust, like Claude Code. Kept as a
# kill-switch so the old prefix-gate behavior can be restored without code edits.
PLUGIN_REQUIRE_INTENT = _env_bool("PLUGIN_REQUIRE_INTENT", False)

# --- Claude Agent SDK (native tool calling) ---
# When True and LLM_PROVIDER=claude, the fast path routes through the Claude
# Agent SDK with an in-process MCP server: the model calls tools natively in an
# agentic loop (no [TOOL:] text-tags, no keyword pre-filter — all tools visible).
# When False, the legacy text-tag claude_cli path is used. Off by default so the
# proven text-tag path stays as instant rollback alongside the pre-seamless-tools tag.
CLAUDE_USE_SDK = _env_bool("CLAUDE_USE_SDK", False)
# Hard timeout (seconds) for one SDK turn. On timeout the persistent client is
# dropped (so a hung CLI can't hold the turn lock) and llm.py falls through to the
# Groq fallback chain. Keep comfortably above normal turn latency (~9-15s).
CLAUDE_SDK_TIMEOUT = _env_int("CLAUDE_SDK_TIMEOUT", 120)
# Effort for the SDK fast path. Empty = don't pass it (some models reject the flag).
CLAUDE_SDK_EFFORT = _env("CLAUDE_SDK_EFFORT", "")
# setting_sources=[] for the SDK: skips ~/.claude plugins/hooks/statusline on boot.
# Off by default — flip only if ~/.claude/settings.json holds nothing auth-related.
CLAUDE_SDK_ISOLATE = _env_bool("CLAUDE_SDK_ISOLATE", False)

# --- Claude speed pack (shared by SDK + CLI paths) ---
CLAUDE_FALLBACK_MODEL = _env("CLAUDE_FALLBACK_MODEL", "haiku")   # "" disables
CLAUDE_MAX_CONCURRENCY = _env_int("CLAUDE_MAX_CONCURRENCY", 3)   # gate on non-code CLI calls
CLAUDE_WARM_POOL = _env_int("CLAUDE_WARM_POOL", 0)               # pre-booted CLI procs per key
CLAUDE_WARM_MAX_IDLE_S = _env_int("CLAUDE_WARM_MAX_IDLE_S", 1200)
LLM_CAPTURE = _env_bool("LLM_CAPTURE", False)                    # logs/llm_calls.jsonl

# --- OpenAI-compatible fallback providers (Groq/local) — not used by Claude ---
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.7)
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 200)

# --- Session summaries (background loop) ---
SUMMARY_IDLE_MINUTES = _env_int("SUMMARY_IDLE_MINUTES", 30)
SUMMARY_MIN_GAP_SECONDS = _env_int("SUMMARY_MIN_GAP_SECONDS", 1800)
SUMMARY_MAX_MESSAGES = _env_int("SUMMARY_MAX_MESSAGES", 40)

# --- Access tier: the ceiling on what she may do (services/access.py) ---
# administrator = tools + run_shell + code mode | mid = tools + run_shell |
# entry = tools only | nothing = no tools. Unattended turns are capped at entry
# regardless. Finer switches (TOOL_CALL_MODE, SHELL_ENABLED) apply underneath.
ACCESS_LEVEL = _env("ACCESS_LEVEL", "administrator")

# --- Companion shell (run_shell tool) ---
# docker = avatar-shell sidecar confined to /mnt/media (the server)
# host   = the machine the server runs on, cwd SHELL_ROOT (the PC harness; PowerShell on Windows)
SHELL_ENABLED = _env_bool("SHELL_ENABLED", True)
SHELL_BACKEND = _env("SHELL_BACKEND", "docker")
SHELL_CONTAINER = _env("SHELL_CONTAINER", "avatar-shell")
SHELL_ROOT = _env("SHELL_ROOT", "/mnt/media")
SHELL_TIMEOUT = _env_int("SHELL_TIMEOUT", 30)
SHELL_MAX_OUTPUT = _env_int("SHELL_MAX_OUTPUT", 8000)
NEXTCLOUD_CONTAINER = _env("NEXTCLOUD_CONTAINER", "nextcloud")
NEXTCLOUD_DATA_DIR = _env("NEXTCLOUD_DATA_DIR", "/mnt/media/nextcloud")

# --- TTS ---
TTS_ENABLED = _env_bool("TTS_ENABLED", True)
TTS_ENGINE = _env("TTS_ENGINE", "omnivoice")
TTS_LANGUAGE = "en"  # Runtime toggle: "en" or "jp". Changed via /language command.
TTS_MIN_CHUNK_CHARS = _env_int("TTS_MIN_CHUNK_CHARS", 40)  # merge shorter TTS sentences

# OmniVoice settings (primary — k2-fsa/OmniVoice, 600+ langs, ~4x faster than CV3)
# Emotion is mapped to OmniVoice's instruct whitelist (pitch/whisper combos).
OMNIVOICE_API_URL = _env("OMNIVOICE_API_URL", "http://localhost:9192")
OMNIVOICE_TIMEOUT = _env_int("OMNIVOICE_TIMEOUT", 20)
# Per-character cloned voice: sent as the TTS server's `ref_audio` (a path under its
# ref roots). "" = the server's startup default voice.
TTS_VOICE_REF = _env("TTS_VOICE_REF", "")

# CosyVoice 3 settings (kept for rollback via TTS_ENGINE=cosyvoice)
COSYVOICE_API_URL = _env("COSYVOICE_API_URL", "http://localhost:9191")
COSYVOICE_TIMEOUT = _env_int("COSYVOICE_TIMEOUT", 30)

# Kokoro settings (fallback — CPU on Ubuntu)
TTS_MODEL_PATH = _env("TTS_MODEL_PATH", "models/kokoro-v1_0.onnx")
TTS_VOICE = _env("TTS_VOICE", "af_heart")
TTS_SPEED = _env_float("TTS_SPEED", 1.0)

# Qwen3-TTS settings (legacy — kept for fallback if needed)
QWEN3TTS_API_URL = _env("QWEN3TTS_API_URL", "http://127.0.0.1:9090")
QWEN3TTS_LANGUAGE = _env("QWEN3TTS_LANGUAGE", "English")
QWEN3TTS_TIMEOUT = _env_int("QWEN3TTS_TIMEOUT", 60)

# --- Cloud TTS (HuggingFace Space) ---
HF_SPACE_URL = _env("HF_SPACE_URL", "")
HF_SPACE_TIMEOUT = _env_int("HF_SPACE_TIMEOUT", 90)

# --- RVC (voice conversion — disabled by default) ---
RVC_ENABLED = _env_bool("RVC_ENABLED", False)
RVC_MODEL_PATH = _env("RVC_MODEL_PATH", "models/rvc/model.pth")
RVC_INDEX_PATH = _env("RVC_INDEX_PATH", "models/rvc/model.index")
RVC_DEVICE = _env("RVC_DEVICE", "cuda:0")
RVC_F0_METHOD = _env("RVC_F0_METHOD", "rmvpe")
RVC_PITCH_SHIFT = _env_int("RVC_PITCH_SHIFT", 0)
RVC_INDEX_RATE = _env_float("RVC_INDEX_RATE", 0.5)
RVC_FILTER_RADIUS = _env_int("RVC_FILTER_RADIUS", 3)
RVC_RMS_MIX_RATE = _env_float("RVC_RMS_MIX_RATE", 0.0)
RVC_PROTECT = _env_float("RVC_PROTECT", 0.33)
RVC_RESAMPLE_SR = _env_int("RVC_RESAMPLE_SR", 0)
RVC_OUTPUT_SR = _env_int("RVC_OUTPUT_SR", 24000)

# --- Memory (SQLite) ---
MEMORY_DB_PATH = _env("MEMORY_DB_PATH", _under(DATA_DIR, "memory.db"))
MEMORY_CONTEXT_HOURS = _env_int("MEMORY_CONTEXT_HOURS", 6)
MEMORY_CONTEXT_SOFT_CAP = _env_int("MEMORY_CONTEXT_SOFT_CAP", 100)
MEMORY_CLEANUP_DAYS = _env_int("MEMORY_CLEANUP_DAYS", 30)

# --- Audio Cleanup ---
AUDIO_KEEP_COUNT = _env_int("AUDIO_KEEP_COUNT", 20)

# --- STT ---
STT_ENABLED = _env_bool("STT_ENABLED", True)
STT_MODEL_SIZE = _env("STT_MODEL_SIZE", "base")

# --- Idle Talk ---
IDLE_TALK_INTERVAL_HOURS = _env_float("IDLE_TALK_INTERVAL_HOURS", 0.5)

# --- Network ---
LUCKY_DRAW_URL = _env("LUCKY_DRAW_URL", "http://localhost:8804")
TASK_REMINDER_POLL_SECONDS = _env_int("TASK_REMINDER_POLL_SECONDS", 60)
# BACKGROUND_POLL_SECONDS is the current name for the background loop's cycle
# length; TASK_REMINDER_POLL_SECONDS is kept for anyone with the old var set —
# it wins as the default when BACKGROUND_POLL_SECONDS itself is unset.
BACKGROUND_POLL_SECONDS = _env_int("BACKGROUND_POLL_SECONDS", TASK_REMINDER_POLL_SECONDS)

# --- Weather (Open-Meteo, free, no API key) ---
WEATHER_LAT = _env_float("WEATHER_LAT", 0.0)
WEATHER_LON = _env_float("WEATHER_LON", 0.0)
WEATHER_LOCATION_NAME = _env("WEATHER_LOCATION_NAME", "Unknown")

# --- Telegram Push Notifications ---
TELEGRAM_NOTIFY_ENABLED = _env_bool("TELEGRAM_NOTIFY_ENABLED", True)
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")

# --- Apprise fan-out (extra notification targets beyond Telegram) ---
# Telegram stays the primary path: only it can pin messages and send stickers.
# Apprise mirrors the plain notify() text to anything else (Discord, desktop, ntfy...).
# URLs embed webhook tokens, so they live in .env ONLY — never in the settings
# registry, which GET /admin/config exposes (openly, when ADMIN_KEY is unset). Empty = no fan-out.
APPRISE_ENABLED = _env_bool("APPRISE_ENABLED", True)
APPRISE_URLS = _env_list("APPRISE_URLS", "")

# --- Virtual office (agent-os-exec) ---
# The executive + workers run as their own service; this harness only hands work over and
# relays the outbox. Empty URL = no office (the clone default): the plugin fails closed.
OFFICE_EXEC_URL = _env("OFFICE_EXEC_URL", "").rstrip("/")
OFFICE_EXEC_KEY = _env("OFFICE_EXEC_KEY", "")

# --- FreshRSS (Google Reader API) ---
FRESHRSS_ENABLED = _env_bool("FRESHRSS_ENABLED", True)
FRESHRSS_URL = _env("FRESHRSS_URL", "")
FRESHRSS_USER = _env("FRESHRSS_USER", "")
FRESHRSS_API_PASSWORD = _env("FRESHRSS_API_PASSWORD", "")

# --- Smart Home (Tuya Cloud / BARDI) ---
TUYA_ACCESS_ID = _env("TUYA_ACCESS_ID", "")
TUYA_ACCESS_SECRET = _env("TUYA_ACCESS_SECRET", "")
TUYA_API_REGION = _env("TUYA_API_REGION", "in")  # in, us, eu, cn, sg

# --- Nextcloud Calendar (CalDAV) ---
NEXTCLOUD_ENABLED = _env_bool("NEXTCLOUD_ENABLED", True)
NEXTCLOUD_CALDAV_URL = _env("NEXTCLOUD_CALDAV_URL", "")
NEXTCLOUD_USER = _env("NEXTCLOUD_USER", "")
NEXTCLOUD_PASSWORD = _env("NEXTCLOUD_PASSWORD", "")
NEXTCLOUD_CALENDAR_NAME = _env("NEXTCLOUD_CALENDAR_NAME", "personal")

# --- Mood System ---
MOOD_ENABLED = _env_bool("MOOD_ENABLED", True)
MOOD_DEFAULT = _env_int("MOOD_DEFAULT", 100)
MOOD_EQUILIBRIUM = _env_int("MOOD_EQUILIBRIUM", 70)
MOOD_DECAY_RATE = _env_float("MOOD_DECAY_RATE", 1.0)    # points per 60s cycle toward equilibrium
MOOD_IDLE_DECAY = _env_float("MOOD_IDLE_DECAY", 0.5)     # extra decay when ignored >2h
MOOD_DISOBEY_THRESHOLD = _env_int("MOOD_DISOBEY_THRESHOLD", 30)   # below this, chance to refuse tools
MOOD_DISOBEY_CHANCE = _env_float("MOOD_DISOBEY_CHANCE", 0.0)      # 0.0 = she always complies

# --- Offline Fallback ---
OFFLINE_REPLY = _env("OFFLINE_REPLY", "Nn... my head's a bit foggy right now. Something's off with the connection. Try again in a bit.")

# --- Touch Interaction ---
TOUCH_ENABLED = _env_bool("TOUCH_ENABLED", False)

# --- Stickers ---
STICKER_ENABLED = _env_bool("STICKER_ENABLED", True)
STICKER_CHANCE = _env_float("STICKER_CHANCE", 0.5)
STICKER_MAP_PATH = _env("STICKER_MAP_PATH", _under(DATA_DIR, "stickers.json"))

# --- Claude CLI ---
CLAUDE_CLI_TIMEOUT = _env_int("CLAUDE_CLI_TIMEOUT", 60)
CLAUDE_CLI_EFFORT = _env("CLAUDE_CLI_EFFORT", "medium")

# Smart mode
CLAUDE_CLI_SMART_MODEL = _env("CLAUDE_CLI_SMART_MODEL", "sonnet")
CLAUDE_CLI_SMART_EFFORT = _env("CLAUDE_CLI_SMART_EFFORT", "medium")
CLAUDE_CLI_SMART_TIMEOUT = _env_int("CLAUDE_CLI_SMART_TIMEOUT", 120)
CLAUDE_CLI_SMART_TOOLS = _env("CLAUDE_CLI_SMART_TOOLS", "WebSearch,WebFetch,Read")

# Code mode (gated by ACCESS_LEVEL — see services/access.py)
CLAUDE_CLI_CODE_MODEL = _env("CLAUDE_CLI_CODE_MODEL", "opus")
CLAUDE_CLI_CODE_EFFORT = _env("CLAUDE_CLI_CODE_EFFORT", "high")
CLAUDE_CLI_CODE_TIMEOUT = _env_int("CLAUDE_CLI_CODE_TIMEOUT", 1800)
CLAUDE_CLI_CODE_TOOLS = _env("CLAUDE_CLI_CODE_TOOLS", "Bash,Read,Write,Edit,Glob,Grep")
CLAUDE_CLI_SANDBOX_PATH = _env("CLAUDE_CLI_SANDBOX_PATH", "/home/user/companion-sandbox")

# Smart idle
CLAUDE_CLI_SMART_IDLE_ENABLED = _env_bool("CLAUDE_CLI_SMART_IDLE_ENABLED", True)
CLAUDE_CLI_SMART_IDLE_CHANCE = _env_float("CLAUDE_CLI_SMART_IDLE_CHANCE", 0.3)
CLAUDE_CLI_SMART_IDLE_TOPICS = _env("CLAUDE_CLI_SMART_IDLE_TOPICS", "")

# --- Agent CLI Fallback ---
# Second agentic-CLI backend, tried when the primary CLI fails (rate limit,
# auth, outage) before dropping to the plain-API fallback chain. Set to
# "opencode" to enable; empty disables it entirely.
AGENT_CLI_FALLBACK = _env("AGENT_CLI_FALLBACK", "")

# --- OpenCode CLI ---
# Auth is owned by the binary (`opencode auth login`) — no API key here.
OPENCODE_BIN = _env("OPENCODE_BIN", "opencode")
# Empty = let opencode use whatever model its own config defaults to, so a
# working `opencode run` on the host keeps working here without duplicating
# the model name in two places.
OPENCODE_MODEL = _env("OPENCODE_MODEL", "")
OPENCODE_VARIANT = _env("OPENCODE_VARIANT", "")  # provider reasoning effort, e.g. "high"
OPENCODE_TIMEOUT = _env_int("OPENCODE_TIMEOUT", 120)
OPENCODE_AGENT = _env("OPENCODE_AGENT", "")  # agent name from opencode.json
# Code mode runs in the same sandbox the Claude path uses — one directory,
# one source of truth.
OPENCODE_CODE_MODEL = _env("OPENCODE_CODE_MODEL", "") or OPENCODE_MODEL
OPENCODE_CODE_TIMEOUT = _env_int("OPENCODE_CODE_TIMEOUT", 1800)

# --- Character Identity (loaded from character.json) ---
CHARACTER_NAME = "Assistant"
CHARACTER_SHORT_NAME = "Assistant"  # card "short_name", else the full name — {{CHARACTER_SHORT_NAME}}
CHARACTER_NICKNAMES: list[str] = []
CHARACTER_FEED_KEYWORDS: list[str] = []
CHARACTER_TAGS: list[str] = []
CHARACTER_SONGS: list[str] = []          # song titles for the spotify plugin
CHARACTER_SONG_ALIASES: dict = {}        # {alias: canonical title}


def load_character():
    """Load character identity from character.json into config globals."""
    global CHARACTER_NAME, CHARACTER_NICKNAMES, CHARACTER_FEED_KEYWORDS, CHARACTER_TAGS
    global CLAUDE_CLI_SMART_IDLE_TOPICS, CHARACTER_SHORT_NAME, OWNER_NAME, OWNER_PRONOUN
    global CHARACTER_SONGS, CHARACTER_SONG_ALIASES
    global TIMEZONE, TZ_LABEL, LANGUAGE, LOCALE_COUNTRY

    import json
    from pathlib import Path

    card_path = Path(CHARACTER_CARD_PATH)
    if not card_path.exists():
        return

    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
        CHARACTER_NAME = card.get("name", "Assistant")
        CHARACTER_SHORT_NAME = card.get("short_name") or CHARACTER_NAME
        CHARACTER_NICKNAMES = card.get("nickname", [])
        owner = card.get("owner") or {}
        if os.environ.get("OWNER_NAME") is None and owner.get("name"):
            OWNER_NAME = owner["name"]
        if os.environ.get("OWNER_PRONOUN") is None and owner.get("pronoun"):
            OWNER_PRONOUN = owner["pronoun"]
        CHARACTER_FEED_KEYWORDS = card.get("feed_keywords", [])
        CHARACTER_TAGS = card.get("tags", [])
        CHARACTER_SONGS = card.get("songs", [])
        CHARACTER_SONG_ALIASES = card.get("song_aliases", {})

        # Locale fields from the card — env vars still take precedence.
        if os.environ.get("TIMEZONE") is None and card.get("timezone"):
            TIMEZONE = card["timezone"]
        if os.environ.get("TZ_LABEL") is None and card.get("tz_label"):
            TZ_LABEL = card["tz_label"]
        if os.environ.get("CHARACTER_LANGUAGE") is None and card.get("language"):
            LANGUAGE = card["language"]
        if os.environ.get("LOCALE_COUNTRY") is None and card.get("country"):
            LOCALE_COUNTRY = card["country"]

        # Derive smart idle topics from tags if not explicitly set
        if not CLAUDE_CLI_SMART_IDLE_TOPICS and CHARACTER_TAGS:
            CLAUDE_CLI_SMART_IDLE_TOPICS = ",".join(CHARACTER_TAGS)
    except Exception:
        pass


# --- CORS ---------------------------------------------------------------------
# No in-repo browser client calls this API cross-origin today. Empty by default;
# set CORS_ORIGINS if a future browser client needs it. CORS middleware is global,
# so this list stays EXACT — a wildcard would expose /admin/* to any origin the
# browser loads.
CORS_ORIGINS = _env_list("CORS_ORIGINS", "")

# Shared secret required on every /admin/* route (header X-Admin-Key; the Telegram bot
# sends it as AVATAR_ADMIN_KEY). Empty means open so a fresh clone just works — set it
# on any host other machines can reach. Env-only: never a settings_registry row.
ADMIN_KEY = os.getenv("ADMIN_KEY", "")
