"""Runtime settings registry — THE single source of truth for /settings and /set.

Every setting that POST /admin/config may change is declared once here: where it
lives (module + attribute), its type, limits, group and help text. Everything else
derives from this table:
- routers/admin.py        validation (coerce) + GET schema
- runtime_settings.py     which keys persist to data/runtime_config.json
- telegram-bot /settings  rendered from the GET schema (no hardcoded key list)

Code reads the live module attribute at call time (e.g. config.LLM_TEMPERATURE), so a
change takes effect on the next use. Adding a knob = one Setting row + read the attr.
"""

import asyncio
import importlib
import logging
from dataclasses import dataclass
from typing import Any, Callable

from services import access

logger = logging.getLogger(__name__)


async def _broadcast_touch(value: bool) -> None:
    from services.ws_manager import manager
    if manager.client_count > 0:
        await manager.broadcast({"type": "config", "touch_enabled": value})


@dataclass(frozen=True)
class Setting:
    key: str
    module: str
    attr: str
    type: type
    group: str
    help: str
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None
    on_change: Callable[[Any], Any] | None = None
    provider: str | None = None  # only meaningful when config.LLM_PROVIDER is this (None = always)


_EFFORTS = ("low", "medium", "high")

SETTINGS: tuple[Setting, ...] = (
    # --- Voice ---
    Setting("tts_enabled", "config", "TTS_ENABLED", bool, "Voice", "Text-to-speech on/off"),
    Setting("stt_enabled", "config", "STT_ENABLED", bool, "Voice", "Speech-to-text on/off"),
    Setting("tts_language", "config", "TTS_LANGUAGE", str, "Voice", "Spoken language", choices=("en", "jp")),
    Setting("tts_min_chunk_chars", "config", "TTS_MIN_CHUNK_CHARS", int, "Voice",
            "TTS sentences shorter than this merge with the next", 0, 200),
    # --- Behavior ---
    Setting("idle_talk_hours", "config", "IDLE_TALK_INTERVAL_HOURS", float, "Behavior",
            "Hours between idle talk", 0.1, 24),
    Setting("idle_tool_chance", "services.background", "IDLE_TOOL_CHANCE", float, "Behavior",
            "Chance an idle talk uses a tool/plugin prompt", 0, 1),
    Setting("sticker_chance", "config", "STICKER_CHANCE", float, "Behavior", "Chance to send a sticker", 0, 1),
    Setting("touch_enabled", "config", "TOUCH_ENABLED", bool, "Behavior", "Tap reactions on the tablet",
            on_change=_broadcast_touch),
    Setting("tool_call_mode", "config", "TOOL_CALL_MODE", str, "Behavior", "Which tools she may call",
            choices=("normal", "semi_normal", "semi_off", "off")),
    Setting("shell_enabled", "config", "SHELL_ENABLED", bool, "Behavior",
            "run_shell tool (media-drive sidecar or the host, per SHELL_BACKEND)"),
    Setting("access_level", "config", "ACCESS_LEVEL", str, "Behavior",
            "Ceiling on what she may do: administrator = tools + shell + code mode, "
            "mid = tools + shell, entry = tools only, nothing = no tools",
            choices=access.TIERS),
    # Only the on/off switch is exposed. The target URLs stay in .env: they embed
    # webhook tokens, and /admin/config is open whenever ADMIN_KEY is unset.
    Setting("apprise_enabled", "config", "APPRISE_ENABLED", bool, "Behavior",
            "Mirror notifications to the extra Apprise targets set in .env"),
    # --- Sleep ---
    Setting("sleep_hour", "services.sleep", "SLEEP_HOUR", int, "Sleep", "Sleep starts (local hour)", 0, 23),
    Setting("wake_hour", "services.sleep", "WAKE_HOUR", int, "Sleep", "Wake up (local hour)", 0, 23),
    # --- Memory ---
    Setting("memory_context_hours", "config", "MEMORY_CONTEXT_HOURS", int, "Memory",
            "Chat history window (hours)", 1, 168),
    Setting("memory_soft_cap", "config", "MEMORY_CONTEXT_SOFT_CAP", int, "Memory",
            "Max history messages in the prompt", 10, 500),
    Setting("summary_idle_minutes", "config", "SUMMARY_IDLE_MINUTES", int, "Memory",
            "Idle minutes before a session summary", 5, 240),
    Setting("summary_max_messages", "config", "SUMMARY_MAX_MESSAGES", int, "Memory",
            "Messages fed into a session summary", 10, 200),
    # --- Brain (Claude) ---
    Setting("sdk_effort", "config", "CLAUDE_SDK_EFFORT", str, "Brain",
            "Effort for normal chat (empty = model default)", choices=("",) + _EFFORTS, provider="claude"),
    Setting("cli_effort", "config", "CLAUDE_CLI_EFFORT", str, "Brain",
            "Effort for CLI fast-mode/fallback calls", choices=_EFFORTS, provider="claude"),
    Setting("fallback_model", "config", "CLAUDE_FALLBACK_MODEL", str, "Brain",
            "Model used when the main one is overloaded (empty = off)", choices=("", "haiku", "sonnet"), provider="claude"),
    Setting("max_concurrency", "config", "CLAUDE_MAX_CONCURRENCY", int, "Brain",
            "Parallel claude CLI processes (code mode exempt)", 1, 6, provider="claude"),
    Setting("warm_pool", "config", "CLAUDE_WARM_POOL", int, "Brain",
            "Pre-booted CLI processes per call type", 0, 4, provider="claude"),
    Setting("llm_capture", "config", "LLM_CAPTURE", bool, "Brain", "Log full prompts to logs/llm_calls.jsonl", provider="claude"),
    # --- Fallback LLM (Groq / local — never Claude) ---
    Setting("llm_temperature", "config", "LLM_TEMPERATURE", float, "Fallback LLM",
            "Temperature for fallback providers", 0, 2),
    Setting("llm_max_tokens", "config", "LLM_MAX_TOKENS", int, "Fallback LLM",
            "Max reply tokens for fallback providers", 50, 4000),
    Setting("llm_timeout", "config", "LLM_TIMEOUT", int, "Fallback LLM",
            "Request timeout for fallback providers (s)", 5, 120),
)

REGISTRY: dict[str, Setting] = {s.key: s for s in SETTINGS}

# Code/env defaults, captured before persisted overrides are applied (see capture_defaults).
_defaults: dict[str, Any] = {}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_EMPTY = {"", "none", "off", "default"}


def _module(s: Setting):
    return importlib.import_module(s.module)


def capture_defaults() -> None:
    """Remember current values as defaults. Call once before applying overrides."""
    for s in SETTINGS:
        if s.key not in _defaults:
            _defaults[s.key] = getattr(_module(s), s.attr, None)


def get(key: str) -> Any:
    s = REGISTRY[key]
    return getattr(_module(s), s.attr, None)


def coerce(key: str, raw: Any) -> Any:
    """Validate + convert a raw value (JSON value or Telegram string). Raises ValueError."""
    s = REGISTRY.get(key)
    if s is None:
        raise ValueError(f"unknown setting '{key}'")

    if s.type is bool:
        if isinstance(raw, bool):
            value = raw
        elif str(raw).strip().lower() in _TRUE:
            value = True
        elif str(raw).strip().lower() in _FALSE:
            value = False
        else:
            raise ValueError(f"{key} must be on/off")
    elif s.type is int:
        try:
            num = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number")
        if isinstance(raw, bool) or not num.is_integer():
            raise ValueError(f"{key} must be a whole number")
        value = int(num)
    elif s.type is float:
        if isinstance(raw, bool):
            raise ValueError(f"{key} must be a number")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
    else:
        value = "" if raw is None else str(raw).strip()
        if s.choices and "" in s.choices and value.lower() in _EMPTY:
            value = ""

    if s.choices is not None and value not in s.choices:
        shown = "|".join(c or "none" for c in s.choices)
        raise ValueError(f"{key} must be one of {shown}")
    if s.min is not None and value < s.min:
        raise ValueError(f"{key} must be {s.min:g}-{s.max:g}")
    if s.max is not None and value > s.max:
        raise ValueError(f"{key} must be {s.min:g}-{s.max:g}")
    return value


def assign(key: str, value: Any) -> None:
    """Set an already-coerced value on its module (no side effects)."""
    s = REGISTRY[key]
    setattr(_module(s), s.attr, value)


async def change(key: str, raw: Any) -> Any:
    """Coerce, assign and run the setting's on_change hook. Returns the new value."""
    value = coerce(key, raw)
    assign(key, value)
    hook = REGISTRY[key].on_change
    if hook:
        result = hook(value)
        if asyncio.iscoroutine(result):
            await result
    return value


def default(key: str) -> Any:
    capture_defaults()
    return _defaults.get(key)


def snapshot() -> dict[str, Any]:
    return {s.key: get(s.key) for s in SETTINGS}


def applies(s: Setting) -> bool:
    """Whether a setting does anything with the current LLM provider."""
    import config
    return s.provider is None or s.provider == config.LLM_PROVIDER


def schema(overridden: set[str] | None = None) -> list[dict]:
    """Setting descriptions + live values, for GET /admin/config and the Telegram bot."""
    capture_defaults()
    overridden = overridden or set()
    return [
        {
            "key": s.key, "group": s.group, "type": s.type.__name__, "help": s.help,
            "min": s.min, "max": s.max, "choices": list(s.choices) if s.choices else None,
            "value": get(s.key), "default": _defaults.get(s.key),
            "overridden": s.key in overridden,
            "applies": applies(s),
        }
        for s in SETTINGS
    ]
