"""Avatar Server Configuration

Reads from .env file if present, falls back to defaults.
Secrets and network IPs should be set in .env (see .env.example).
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
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

# --- Server ---
AVATAR_SERVER_HOST = _env("AVATAR_SERVER_HOST", "0.0.0.0")
AVATAR_SERVER_PORT = _env_int("AVATAR_SERVER_PORT", 8800)

# --- LLM Provider ---
LLM_PROVIDER = _env("LLM_PROVIDER", "lmstudio")
LLM_BASE_URL = _env("LLM_BASE_URL", "") or _env("LM_STUDIO_BASE_URL", "")  # legacy compat
LLM_MODEL = _env("LLM_MODEL", "") or _env("LM_STUDIO_MODEL", "")  # legacy compat
LLM_API_KEY = _env("LLM_API_KEY", "")
LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 20)

# --- LLM Fallback Chain ---
LLM_FALLBACK_1 = _env("LLM_FALLBACK_1", "")  # e.g. "groq"
LLM_FALLBACK_1_API_KEY = _env("LLM_FALLBACK_1_API_KEY", "")
LLM_FALLBACK_2 = _env("LLM_FALLBACK_2", "")  # e.g. "cerebras"
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
        "model": "llama-3.3-70b-versatile",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "api_key": "",
        "model": "mistral-medium-latest",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "api_key": "",
        "model": "llama3.1-8b",
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
        defaults = _PROVIDER_DEFAULTS.get(fb_provider, {})
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

# --- Emotion Tags ---
VALID_EMOTIONS = ["HAPPY", "SAD", "SURPRISED", "ANGRY", "THINKING", "NEUTRAL"]
DEFAULT_EMOTION = "NEUTRAL"

# --- TTS ---
TTS_ENABLED = _env_bool("TTS_ENABLED", True)
TTS_ENGINE = _env("TTS_ENGINE", "cosyvoice")
TTS_LANGUAGE = "en"  # Runtime toggle: "en" or "jp". Changed via /language command.

# CosyVoice 3 settings (primary — voice clone + emotions + Japanese)
COSYVOICE_API_URL = _env("COSYVOICE_API_URL", "http://100.83.33.113:9091")
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
HF_SPACE_URL = _env("HF_SPACE_URL", "venomaru/suisei-tts")
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
MEMORY_DB_PATH = _env("MEMORY_DB_PATH", "data/memory.db")
MEMORY_CONTEXT_HOURS = _env_int("MEMORY_CONTEXT_HOURS", 6)
MEMORY_CONTEXT_SOFT_CAP = _env_int("MEMORY_CONTEXT_SOFT_CAP", 100)
MEMORY_CLEANUP_DAYS = _env_int("MEMORY_CLEANUP_DAYS", 30)

# --- Audio Cleanup ---
AUDIO_KEEP_COUNT = _env_int("AUDIO_KEEP_COUNT", 5)

# --- STT ---
STT_ENABLED = _env_bool("STT_ENABLED", True)
STT_MODEL_SIZE = _env("STT_MODEL_SIZE", "base")

# --- Idle Talk ---
IDLE_TALK_INTERVAL_HOURS = _env_float("IDLE_TALK_INTERVAL_HOURS", 0.5)

# --- Network ---
REDMI_BOT_URL = _env("REDMI_BOT_URL", "http://localhost:3001")
UBUNTU_API_URL = _env("UBUNTU_API_URL", "http://localhost")
TASK_MANAGER_URL = _env("TASK_MANAGER_URL", "http://localhost:8801")
MONEY_MANAGER_URL = _env("MONEY_MANAGER_URL", "http://localhost:8802")
CALORIE_TRACKER_URL = _env("CALORIE_TRACKER_URL", "http://localhost:8803")
MEME_SERVICE_URL = _env("MEME_SERVICE_URL", "http://localhost:8807")
LUCKY_DRAW_URL = _env("LUCKY_DRAW_URL", "http://localhost:8804")
TASK_REMINDER_POLL_SECONDS = _env_int("TASK_REMINDER_POLL_SECONDS", 60)

# --- Weather (Open-Meteo, free, no API key) ---
WEATHER_LAT = _env_float("WEATHER_LAT", 0.0)
WEATHER_LON = _env_float("WEATHER_LON", 0.0)
WEATHER_LOCATION_NAME = _env("WEATHER_LOCATION_NAME", "Unknown")

# --- Telegram Push Notifications ---
TELEGRAM_NOTIFY_ENABLED = _env_bool("TELEGRAM_NOTIFY_ENABLED", True)
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID", "")

# --- FreshRSS (Google Reader API) ---
FRESHRSS_ENABLED = _env_bool("FRESHRSS_ENABLED", True)
FRESHRSS_URL = _env("FRESHRSS_URL", "")
FRESHRSS_USER = _env("FRESHRSS_USER", "")
FRESHRSS_API_PASSWORD = _env("FRESHRSS_API_PASSWORD", "")

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
MOOD_DISOBEY_THRESHOLD = 30     # below this, chance to refuse tools
MOOD_DISOBEY_CHANCE = 0.15      # 15% chance at mood <=30

# --- Offline Fallback ---
OFFLINE_REPLY = _env("OFFLINE_REPLY", "Nn... my head's a bit foggy right now. Something's off with the connection. Try again in a bit.")

# --- Touch Interaction ---
TOUCH_ENABLED = _env_bool("TOUCH_ENABLED", False)

# --- Stickers ---
STICKER_ENABLED = _env_bool("STICKER_ENABLED", True)
STICKER_CHANCE = _env_float("STICKER_CHANCE", 0.5)
STICKER_MAP_PATH = _env("STICKER_MAP_PATH", "data/stickers.json")

# --- Claude CLI ---
CLAUDE_CLI_TIMEOUT = _env_int("CLAUDE_CLI_TIMEOUT", 60)
CLAUDE_CLI_EFFORT = _env("CLAUDE_CLI_EFFORT", "medium")

# Smart mode
CLAUDE_CLI_SMART_MODEL = _env("CLAUDE_CLI_SMART_MODEL", "sonnet")
CLAUDE_CLI_SMART_EFFORT = _env("CLAUDE_CLI_SMART_EFFORT", "medium")
CLAUDE_CLI_SMART_TIMEOUT = _env_int("CLAUDE_CLI_SMART_TIMEOUT", 120)
CLAUDE_CLI_SMART_TOOLS = _env("CLAUDE_CLI_SMART_TOOLS", "WebSearch,WebFetch,Read")

# Code mode
CLAUDE_CLI_CODE_MODEL = _env("CLAUDE_CLI_CODE_MODEL", "opus")
CLAUDE_CLI_CODE_EFFORT = _env("CLAUDE_CLI_CODE_EFFORT", "high")
CLAUDE_CLI_CODE_TIMEOUT = _env_int("CLAUDE_CLI_CODE_TIMEOUT", 300)
CLAUDE_CLI_CODE_TOOLS = _env("CLAUDE_CLI_CODE_TOOLS", "Bash,Read,Write,Edit,Glob,Grep")
CLAUDE_CLI_SANDBOX_PATH = _env("CLAUDE_CLI_SANDBOX_PATH", "/home/venomaru/companion-sandbox")

# Smart idle
CLAUDE_CLI_SMART_IDLE_ENABLED = _env_bool("CLAUDE_CLI_SMART_IDLE_ENABLED", True)
CLAUDE_CLI_SMART_IDLE_CHANCE = _env_float("CLAUDE_CLI_SMART_IDLE_CHANCE", 0.3)
CLAUDE_CLI_SMART_IDLE_TOPICS = _env("CLAUDE_CLI_SMART_IDLE_TOPICS", "")

# --- Character Identity (loaded from character.json) ---
CHARACTER_NAME = "Assistant"
CHARACTER_NICKNAMES: list[str] = []
CHARACTER_FEED_KEYWORDS: list[str] = []
CHARACTER_TAGS: list[str] = []


def load_character():
    """Load character identity from character.json into config globals."""
    global CHARACTER_NAME, CHARACTER_NICKNAMES, CHARACTER_FEED_KEYWORDS, CHARACTER_TAGS
    global CLAUDE_CLI_SMART_IDLE_TOPICS

    import json
    from pathlib import Path

    card_path = Path(CHARACTER_CARD_PATH)
    if not card_path.exists():
        return

    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
        CHARACTER_NAME = card.get("name", "Assistant")
        CHARACTER_NICKNAMES = card.get("nickname", [])
        CHARACTER_FEED_KEYWORDS = card.get("feed_keywords", [])
        CHARACTER_TAGS = card.get("tags", [])

        # Derive smart idle topics from tags if not explicitly set
        if not CLAUDE_CLI_SMART_IDLE_TOPICS and CHARACTER_TAGS:
            CLAUDE_CLI_SMART_IDLE_TOPICS = ",".join(CHARACTER_TAGS)
    except Exception:
        pass
