"""Persist runtime /settings across restarts.

`/settings` and `/set` (via POST /admin/config) mutate in-memory module
attributes (config.*, sleep.*, background.*). On a container rebuild those reload
to code defaults, so every deploy wiped the user's settings. This stores the
overridable settings in the data/ Docker volume (survives rebuilds) and reapplies
them at startup, so settings stick across deploys.

- apply(): called once at startup, after config.load_character(), before services
  that read these values initialize.
- save(): called by POST /admin/config after a change; snapshots current values.
"""

import json
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_PATH = Path("data/runtime_config.json")


def _targets() -> dict:
    """key -> (module, attr) for every setting POST /admin/config can change.

    Imported lazily so this module has no import-time dependency on sleep/background.
    """
    from services import sleep, background
    return {
        "stt_enabled":      (config, "STT_ENABLED"),
        "tts_enabled":      (config, "TTS_ENABLED"),
        "idle_talk_hours":  (config, "IDLE_TALK_INTERVAL_HOURS"),
        "sticker_chance":   (config, "STICKER_CHANCE"),
        "touch_enabled":    (config, "TOUCH_ENABLED"),
        "tts_language":     (config, "TTS_LANGUAGE"),
        "tool_call_mode":   (config, "TOOL_CALL_MODE"),
        "idle_tool_chance": (background, "IDLE_TOOL_CHANCE"),
        "sleep_hour":       (sleep, "SLEEP_HOUR"),
        "wake_hour":        (sleep, "WAKE_HOUR"),
    }


def apply() -> None:
    """Load persisted overrides and apply them to the live modules."""
    if not _PATH.exists():
        return
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[runtime_settings] load failed: {e}")
        return
    applied = []
    for key, (mod, attr) in _targets().items():
        if key in data and data[key] is not None:
            setattr(mod, attr, data[key])
            applied.append(f"{key}={data[key]}")
    if applied:
        logger.info(f"[runtime_settings] applied saved overrides: {', '.join(applied)}")


def save() -> None:
    """Snapshot the current overridable settings to disk (data/ volume).

    Some targets (e.g. background.IDLE_TOOL_CHANCE) only exist once set at runtime —
    skip any attr that isn't present yet rather than failing the whole snapshot.
    """
    try:
        data = {
            key: getattr(mod, attr)
            for key, (mod, attr) in _targets().items()
            if hasattr(mod, attr)
        }
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("[runtime_settings] saved")
    except Exception as e:
        logger.warning(f"[runtime_settings] save failed: {e}")
