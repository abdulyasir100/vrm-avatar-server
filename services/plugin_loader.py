"""Plugin loader — scans plugins/, reads manifests, registers tools/routes/idle/background."""

import asyncio
import importlib
import importlib.util
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

import config
from services import tool_registry

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"
_loaded_plugins: dict[str, dict] = {}  # name -> {manifest, handler_module, idle_module, ...}


# Per-instance plugin state lives under DATA_DIR, never in manifest.json (which is in git
# and shared by every instance of this checkout). Paths resolve at call time.
def _state_path() -> Path:
    return Path(config.DATA_DIR) / "plugin_state.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _find_plugin_dir(name: str) -> Path | None:
    """Locate a plugin's folder by its manifest `name` field, not its folder name
    (folder and manifest name can differ, e.g. plugins/plugin-example/ -> "example").

    Prefers the loaded entry's cached dir; otherwise scans _PLUGINS_DIR. Scanning
    ~25 small dirs per call is fine — this isn't a hot path.
    """
    entry = _loaded_plugins.get(name)
    if entry:
        return entry["dir"]
    if not _PLUGINS_DIR.exists():
        return None
    for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir():
            continue
        manifest = _read_json(plugin_dir / "manifest.json")
        if manifest.get("name") == name:
            return plugin_dir
    return None


def _read_manifest(name: str) -> dict | None:
    plugin_dir = _find_plugin_dir(name)
    if plugin_dir is None:
        return None
    try:
        return json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def locked_reason(name: str) -> str:
    return "" if config.plugin_allowed(name) else "not in PLUGINS_ENABLED"


def is_enabled(manifest: dict) -> bool:
    """This instance's choice first, else the manifest's default."""
    state = _read_json(_state_path())
    if manifest["name"] in state.get("disabled", []):
        return False
    if manifest["name"] in state.get("enabled", []):
        return True
    return manifest.get("enabled", True)


def _set_state(name: str, on: bool) -> None:
    state = _read_json(_state_path())
    enabled, disabled = set(state.get("enabled", [])), set(state.get("disabled", []))
    (enabled if on else disabled).add(name)
    (disabled if on else enabled).discard(name)
    _write_json(_state_path(), {"enabled": sorted(enabled), "disabled": sorted(disabled)})

# Intent prefixes required for plugin tool triggers (main features bypass this)
# Built dynamically from config.CHARACTER_NICKNAMES + common action words
_intent_pattern: re.Pattern | None = None


def _build_intent_pattern() -> re.Pattern:
    """Build intent regex: message must START with a character nickname.

    Nicknames loaded from character.json. Matches patterns like:
      "<nickname>, can you add..."
      "<nickname> please check..."
      "hey <nickname>, I ate..."
    """
    import config
    nickname_parts = []
    for nick in (config.CHARACTER_NICKNAMES or []):
        escaped = re.escape(nick).replace(r"\ ", r"[- ]?").replace(r"\-", r"[- ]?")
        nickname_parts.append(escaped)

    if not nickname_parts:
        # No nicknames configured — fall back to action words only
        return re.compile(r"(?:^|\b)(?:please|can you|could you)", re.IGNORECASE)

    # Two intent patterns:
    # 1. Nickname at start (with optional filler): "hey suichan check..."
    # 2. Anything + nickname + comma: "and suichan, I drank..."
    nicks = "|".join(nickname_parts)
    return re.compile(
        rf"^\s*(?:hey|yo|oi|eh)?\s*(?:{nicks})\b|(?:{nicks})\s*,",
        re.IGNORECASE,
    )


def has_intent_prefix(message: str) -> bool:
    """Check if a message starts with a character nickname (intent prefix)."""
    global _intent_pattern
    if _intent_pattern is None:
        _intent_pattern = _build_intent_pattern()
    return bool(_intent_pattern.search(message.strip()))


def load_all_plugins():
    """Scan plugins/ directory and load all enabled plugins."""
    if not _PLUGINS_DIR.exists():
        logger.warning(f"[plugin_loader] No plugins directory at {_PLUGINS_DIR}")
        return

    for plugin_dir in sorted(_PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir():
            continue
        manifest_path = plugin_dir / "manifest.json"
        if not manifest_path.exists():
            logger.warning(f"[plugin_loader] No manifest.json in {plugin_dir.name}, skipping")
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"[plugin_loader] Failed to parse manifest for {plugin_dir.name}: {e}")
            continue

        if not is_enabled(manifest):
            logger.info(f"[plugin_loader] Plugin '{manifest['name']}' is disabled, skipping")
            continue
        if not config.plugin_allowed(manifest["name"]):
            logger.info(f"[plugin_loader] Plugin '{manifest['name']}' not in PLUGINS_ENABLED, skipping")
            continue

        try:
            _load_plugin(plugin_dir, manifest)
        except Exception as e:
            logger.error(f"[plugin_loader] Failed to load plugin '{manifest.get('name', plugin_dir.name)}': {e}")

    logger.info(f"[plugin_loader] Loaded {len(_loaded_plugins)} plugins: {list(_loaded_plugins.keys())}")


def _load_plugin(plugin_dir: Path, manifest: dict):
    """Load a single plugin from its directory."""
    name = manifest["name"]

    entry: dict[str, Any] = {"manifest": manifest, "dir": plugin_dir}

    # Load handler module
    handler_path = plugin_dir / "handler.py"
    if handler_path.exists():
        mod = _import_module(f"plugins.{name}.handler", handler_path)
        entry["handler"] = mod

        # Register tools from manifest
        for tool_def in manifest.get("tools", []):
            tool_name = tool_def["name"]
            handler_fn = getattr(mod, f"handle_{tool_name}", None)
            if handler_fn:
                tool_registry.register(tool_name, handler_fn, schema=tool_def)
                logger.info(f"[plugin_loader] Registered tool: {tool_name} (plugin: {name})")
            else:
                logger.warning(f"[plugin_loader] Tool handler 'handle_{tool_name}' not found in {name}/handler.py")

    # Load idle module if configured
    idle_config = manifest.get("idle", {})
    if idle_config.get("enabled"):
        idle_file = idle_config.get("prompts_file", "idle.py")
        idle_path = plugin_dir / idle_file
        if idle_path.exists():
            idle_mod = _import_module(f"plugins.{name}.idle", idle_path)
            entry["idle"] = idle_mod

    # Load prompt doc
    prompt_path = plugin_dir / "prompt.md"
    if prompt_path.exists():
        entry["prompt_doc"] = prompt_path.read_text(encoding="utf-8")

    # Initialize storage if defined — uses data/ volume for persistence
    storage_config = manifest.get("storage", {})
    if storage_config.get("type") == "sqlite":
        storage_mod_path = plugin_dir / "storage.py"
        if storage_mod_path.exists():
            storage_mod = _import_module(f"plugins.{name}.storage", storage_mod_path)
            entry["storage"] = storage_mod
            db_file = storage_config.get("file", "data.db")
            # Store in data/ volume (persists across Docker rebuilds)
            data_dir = Path(config.PLUGIN_DATA_DIR) / name
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / db_file
            if hasattr(storage_mod, "init"):
                storage_mod.init(str(db_path))
                logger.info(f"[plugin_loader] Initialized storage: {db_path}")
            # Wire storage into handler and idle modules
            handler_mod = entry.get("handler")
            if handler_mod and hasattr(handler_mod, "set_storage"):
                handler_mod.set_storage(storage_mod)
            idle_mod = entry.get("idle")
            if idle_mod and hasattr(idle_mod, "set_storage"):
                idle_mod.set_storage(storage_mod)

    _loaded_plugins[name] = entry


def _import_module(module_name: str, file_path: Path):
    """Import a Python module from a file path."""
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_enabled_plugins() -> dict[str, dict]:
    """Return all loaded (enabled) plugins."""
    return dict(_loaded_plugins)


def get_plugin(name: str) -> dict | None:
    """Get a specific plugin by name."""
    return _loaded_plugins.get(name)


def call_plugin(name: str, attr_path: str, *args, default=None, **kwargs):
    """Generic cross-plugin / engine → plugin call helper.

    Resolves a dotted ``attr_path`` on a loaded plugin and calls it (or returns
    the attribute if it isn't callable). The first segment selects the plugin
    sub-module — "handler" (default), "storage", or "idle" — and the rest walks
    attributes, e.g. ``call_plugin("todo", "storage.get_all", completed=False)``.

    Returns ``default`` if the plugin, sub-module, attribute, or call is
    unavailable. This lets one plugin use another's data (or the engine reach a
    plugin) WITHOUT a static import, so any plugin folder can be deleted safely.
    """
    entry = _loaded_plugins.get(name)
    if not entry:
        return default
    parts = attr_path.split(".")
    # Default to the handler module when the first segment isn't a known sub-module.
    if parts[0] in ("handler", "storage", "idle"):
        obj = entry.get(parts[0])
        parts = parts[1:]
    else:
        obj = entry.get("handler")
    for p in parts:
        if obj is None:
            return default
        obj = getattr(obj, p, None)
    if obj is None:
        return default
    try:
        return obj(*args, **kwargs) if callable(obj) else obj
    except Exception as e:
        logger.debug(f"[plugin_loader] call_plugin({name}, {attr_path}) failed: {e}")
        return default


def get_idle_category_owners() -> dict[str, str]:
    """Map idle/background context labels → owning plugin, built from manifests.

    A plugin declares the background context labels it emits via an optional
    manifest ``idle_categories`` list. Every plugin also implicitly owns its
    ``<name>_check`` context (the default in the background loop). Replaces the
    old hardcoded name table so no plugin is named in engine source.
    """
    owners: dict[str, str] = {}
    for name, entry in _loaded_plugins.items():
        owners[f"{name}_check"] = name
        for cat in entry["manifest"].get("idle_categories", []):
            owners[str(cat)] = name
    return owners


def enable_plugin(name: str) -> bool:
    """Turn a plugin on for this instance and load it live. Load errors propagate and
    leave the state untouched.

    Re-enabling a plugin that isn't currently loaded re-imports its handler module
    from scratch, so any in-memory module state (caches, counters, etc.) resets.
    """
    manifest = _read_manifest(name)
    if manifest is None:
        return False
    if name not in _loaded_plugins:
        plugin_dir = _find_plugin_dir(name)
        _load_plugin(plugin_dir, manifest)
    _set_state(name, True)
    return True


async def disable_plugin(name: str) -> bool:
    """Turn a plugin off for this instance and unregister its tools.

    Calls the handler module's shutdown() first, if it defines one (sync or async —
    same convention as main.py's app-shutdown hook), so it can flush state. An
    exception there is logged, never raised: the toggle itself must always succeed.
    """
    if _read_manifest(name) is None:
        return False
    if name in _loaded_plugins:
        handler = _loaded_plugins[name].get("handler")
        shutdown_fn = getattr(handler, "shutdown", None)
        if callable(shutdown_fn):
            try:
                result = shutdown_fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"[plugin_loader] disable {name}: shutdown() failed: {e!r}")
        for tool_def in _loaded_plugins[name]["manifest"].get("tools", []):
            tool_registry.unregister(tool_def["name"])
        del _loaded_plugins[name]
    _set_state(name, False)
    return True


def list_all() -> list[dict]:
    """Every plugin on disk (loaded or not) for the settings page.

    A manifest whose settings_schema can't be read (e.g. a bad default) still shows
    up with its other fields — only that plugin's row loses its settings.
    """
    rows = []
    seen: set[str] = set()
    for plugin_dir in sorted(_PLUGINS_DIR.iterdir()) if _PLUGINS_DIR.exists() else []:
        if not plugin_dir.is_dir():
            continue
        manifest = _read_json(plugin_dir / "manifest.json")
        name = manifest.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        row = {
            "name": name,
            "description": manifest.get("description", ""),
            "enabled": is_enabled(manifest),
            "loaded": name in _loaded_plugins,
            "locked_reason": locked_reason(name),
            "commands": manifest.get("telegram_commands", []),
        }
        try:
            row["settings"] = get_settings(name)
        except Exception as e:
            logger.warning(f"[plugin_loader] settings failed for {name}: {e!r}")
            row["settings"] = []
            row["error"] = str(e)
        rows.append(row)
    return rows


def get_plugin_triggers() -> list[tuple[str, list[str], bool]]:
    """Return (regex_pattern, tool_names, requires_intent) for each plugin.

    Whether plugins require an intent prefix is driven by
    config.PLUGIN_REQUIRE_INTENT (default False = full-trust). Main features
    (registered elsewhere) never require intent.
    """
    import config

    requires_intent = config.PLUGIN_REQUIRE_INTENT
    triggers = []
    for name, entry in _loaded_plugins.items():
        pattern = entry["manifest"].get("triggers")
        if pattern:
            tool_names = [t["name"] for t in entry["manifest"].get("tools", [])]
            triggers.append((pattern, tool_names, requires_intent))
    return triggers


def get_plugin_prompt_docs() -> dict[str, str]:
    """Return tool_name -> prompt doc text for all plugins."""
    docs = {}
    for name, entry in _loaded_plugins.items():
        doc = entry.get("prompt_doc", "")
        if doc:
            for tool_def in entry["manifest"].get("tools", []):
                docs[tool_def["name"]] = doc
    return docs


def get_plugin_idle_prompts() -> list[str]:
    """Collect idle talk prompts from all enabled plugins."""
    prompts = []
    for name, entry in _loaded_plugins.items():
        idle_mod = entry.get("idle")
        if idle_mod and hasattr(idle_mod, "get_idle_prompts"):
            try:
                plugin_prompts = idle_mod.get_idle_prompts()
                if plugin_prompts:
                    prompts.extend(plugin_prompts)
            except Exception as e:
                logger.error(f"[plugin_loader] Idle prompts failed for {name}: {e}")
    return prompts


async def get_plugin_background_checks() -> list[tuple[str, Callable]]:
    """Collect background check functions from all enabled plugins."""
    checks = []
    for name, entry in _loaded_plugins.items():
        bg_config = entry["manifest"].get("background_check", {})
        if bg_config.get("enabled"):
            fn_name = bg_config.get("function")
            handler_mod = entry.get("handler")
            if handler_mod and fn_name and hasattr(handler_mod, fn_name):
                checks.append((name, getattr(handler_mod, fn_name)))
    return checks


def get_plugin_background_tasks() -> list[tuple[str, Callable, bool, int]]:
    """Collect background task functions from all enabled plugins.

    Returns: list of (plugin_name, async_function, run_during_sleep, interval_seconds)
    Unlike background_checks (which return data), tasks handle their own logic
    (notifications, message queue, etc.)
    """
    tasks = []
    for name, entry in _loaded_plugins.items():
        bt_config = entry["manifest"].get("background_task", {})
        if bt_config.get("enabled"):
            fn_name = bt_config.get("function")
            run_during_sleep = bt_config.get("run_during_sleep", False)
            interval = int(bt_config.get("interval_seconds", 60))
            handler_mod = entry.get("handler")
            if handler_mod and fn_name and hasattr(handler_mod, fn_name):
                tasks.append((name, getattr(handler_mod, fn_name), run_during_sleep, interval))
    return tasks


def get_chat_context_blocks() -> list[str]:
    """Collect live system-prompt context from any plugin whose handler defines
    build_chat_context() -> str.

    This is the generic hook for a plugin to inject up-to-the-moment state into
    the chat system prompt (e.g. the game plugin's most-recent PvP result), so
    the character stays grounded in things that happened outside her chat tools.
    Returns only non-empty blocks. Never raises.
    """
    blocks: list[str] = []
    for name, entry in _loaded_plugins.items():
        handler_mod = entry.get("handler")
        fn = getattr(handler_mod, "build_chat_context", None) if handler_mod else None
        if not fn:
            continue
        try:
            block = fn()
            if block:
                blocks.append(block)
        except Exception as e:
            logger.warning(f"[plugin_loader] build_chat_context failed for {name}: {e!r}")
    return blocks


def notify_turn_end(turn: dict) -> None:
    """Tell every plugin whose handler defines on_turn_end(turn) that a chat turn finished.

    turn = {message, reply, context, user_name, tool, ok}. The generic hook for plugins that
    learn from what happened (the skills plugin records tasks here). Never raises.
    """
    for name, entry in _loaded_plugins.items():
        handler_mod = entry.get("handler")
        fn = getattr(handler_mod, "on_turn_end", None) if handler_mod else None
        if not fn:
            continue
        try:
            fn(turn)
        except Exception as e:
            logger.warning(f"[plugin_loader] on_turn_end failed for {name}: {e!r}")


async def handle_callback(plugin_name: str, action: str, item_id: str) -> dict | None:
    """Handle a Telegram inline button callback for a plugin."""
    entry = _loaded_plugins.get(plugin_name)
    if not entry:
        return None
    handler_mod = entry.get("handler")
    if handler_mod and hasattr(handler_mod, "handle_callback"):
        return await handler_mod.handle_callback(action, item_id)
    return None


# ─── plugin settings ────────────────────────────────────────────────────────
# Declared in manifest.settings_schema. A plugin whose storage module has
# get_setting/set_setting keeps them in its own SQLite settings table (so its chat
# commands and this API edit one value); every other plugin uses
# DATA_DIR/plugin_settings.json. Values are read at call time, so changes are live.

_TYPES = {"int": "int", "integer": "int", "float": "float", "number": "float",
          "bool": "bool", "boolean": "bool"}
_EMPTY = {"int": 0, "float": 0.0, "bool": False, "str": ""}


def _settings_path() -> Path:
    return Path(config.DATA_DIR) / "plugin_settings.json"


def _schema(name: str) -> dict:
    manifest = (_loaded_plugins.get(name) or {}).get("manifest") or _read_manifest(name) or {}
    return manifest.get("settings_schema", {}) or {}


def _spec(name: str, key: str) -> dict:
    spec = _schema(name).get(key)
    if spec is None:
        raise KeyError(f"{name} has no setting {key!r}")
    return spec


def _type(spec: dict) -> str:
    return _TYPES.get(str(spec.get("type", "")).lower(), "str")


def _coerce(spec: dict, raw: Any) -> Any:
    kind = _type(spec)
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"expected true/false, got {raw!r}")
    if kind in ("int", "float"):
        if isinstance(raw, bool):
            raise ValueError(f"expected a number, got {raw!r}")
        try:
            return int(str(raw).strip()) if kind == "int" else float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"expected {kind}, got {raw!r}") from None
    return "" if raw is None else str(raw)


def _default(spec: dict) -> Any:
    if spec.get("default") is None:
        return _EMPTY[_type(spec)]
    return _coerce(spec, spec["default"])


def _store(name: str):
    storage = (_loaded_plugins.get(name) or {}).get("storage")
    if storage and hasattr(storage, "get_setting") and hasattr(storage, "set_setting"):
        return storage
    return None


def _raw(name: str, key: str) -> Any:
    store = _store(name)
    if store:
        return store.get_setting(key)
    return _read_json(_settings_path()).get(name, {}).get(key)


def get_setting(name: str, key: str) -> Any:
    """The typed value of a plugin setting for this instance (override, else manifest default)."""
    spec = _spec(name, key)
    raw = _raw(name, key)
    if raw is None:
        return _default(spec)
    try:
        return _coerce(spec, raw)
    except ValueError:
        logger.warning(f"[plugin_loader] {name}.{key}: unparseable stored value {raw!r}, using default")
        return _default(spec)


def _write(name: str, key: str, value: Any) -> None:
    store = _store(name)
    if store:
        store.set_setting(key, ("1" if value else "0") if isinstance(value, bool) else str(value))
        return
    data = _read_json(_settings_path())
    section = data.get(name) if isinstance(data.get(name), dict) else {}
    section[key] = value
    data[name] = section
    _write_json(_settings_path(), data)


def validate_setting(name: str, key: str, value: Any) -> Any:
    """Typed value if valid for the schema, else ValueError/KeyError. Writes nothing."""
    spec = _spec(name, key)
    typed = _coerce(spec, value)
    options = spec.get("options")
    if options and typed not in options:
        raise ValueError(f"{key} must be one of {options}")
    return typed


def set_setting(name: str, key: str, value: Any) -> Any:
    """Validate against the schema and store. Only for loaded plugins."""
    if name not in _loaded_plugins:
        _spec(name, key)  # an unknown key still reports KeyError first
        raise PermissionError(f"enable {name} before changing its settings")
    typed = validate_setting(name, key, value)
    _write(name, key, typed)
    return typed


def reset_setting(name: str, key: str) -> Any:
    spec = _spec(name, key)
    if name not in _loaded_plugins:
        raise PermissionError(f"enable {name} before changing its settings")
    default = _default(spec)
    if _store(name):
        _write(name, key, default)
    else:
        data = _read_json(_settings_path())
        if isinstance(data.get(name), dict) and key in data[name]:
            del data[name][key]
            _write_json(_settings_path(), data)
    return default


def get_settings(name: str) -> list[dict]:
    """Rows in the same shape as GET /admin/config's schema, so the page reuses one renderer."""
    rows = []
    for key, spec in _schema(name).items():
        value, default, secret = get_setting(name, key), _default(spec), bool(spec.get("secret"))
        rows.append({
            "key": key,
            "type": _type(spec),
            "help": spec.get("label") or spec.get("description", ""),
            "choices": spec.get("options"),
            "min": spec.get("min"),
            "max": spec.get("max"),
            "default": "" if secret else default,
            "value": ("set" if value else "") if secret else value,
            "overridden": value != default,
            "secret": secret,
        })
    return rows
