"""Persist runtime /settings across restarts.

`/settings` and `/set` (via POST /admin/config) mutate in-memory module attributes.
On a container rebuild those reload to code defaults, so this stores the overrides in
the data/ Docker volume (survives rebuilds) and reapplies them at startup.

Which keys exist, their types and limits come from services/settings_registry.py.

- apply(): once at startup, after config.load_character(), before services read values.
  Captures code/env defaults first, then applies each stored value through the registry's
  validation (bad or unknown keys are skipped with a log, never crash startup).
- save(changed): merges ONLY the changed keys into the file. Untouched keys stay absent,
  so later code/env default changes still take effect for them.
- reset(key): drops a key's override (the caller restores the default value).
"""

import json
import logging
import os
from pathlib import Path

import config
from services import settings_registry as registry

logger = logging.getLogger(__name__)

_PATH = Path(config.RUNTIME_CONFIG_PATH)


def _load() -> dict:
    if not _PATH.exists():
        return {}
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"[runtime_settings] load failed: {e}")
        return {}


def _write(data: dict) -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, _PATH)
    except Exception as e:
        logger.warning(f"[runtime_settings] save failed: {e}")


def overridden_keys() -> set[str]:
    return {k for k, v in _load().items() if k in registry.REGISTRY and v is not None}


def apply() -> None:
    """Capture defaults, then apply persisted overrides to the live modules."""
    registry.capture_defaults()
    applied = []
    for key, raw in _load().items():
        if raw is None:
            continue
        if key not in registry.REGISTRY:
            logger.info(f"[runtime_settings] ignoring unknown saved key {key!r}")
            continue
        try:
            value = registry.coerce(key, raw)
        except ValueError as e:
            logger.warning(f"[runtime_settings] skipping saved {key}={raw!r}: {e}")
            continue
        registry.assign(key, value)
        applied.append(f"{key}={value}")
    if applied:
        logger.info(f"[runtime_settings] applied saved overrides: {', '.join(applied)}")


def save(changed: dict) -> None:
    """Merge the changed keys into the persisted overrides."""
    if not changed:
        return
    data = _load()
    data.update(changed)
    _write(data)
    logger.info(f"[runtime_settings] saved {sorted(changed)}")


def reset(key: str) -> None:
    """Remove a key's persisted override."""
    data = _load()
    if data.pop(key, None) is not None:
        _write(data)
        logger.info(f"[runtime_settings] reset {key}")
