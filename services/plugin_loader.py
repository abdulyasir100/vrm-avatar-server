"""Plugin loader — scans plugins/, reads manifests, registers tools/routes/idle/background."""

import importlib
import importlib.util
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from services import tool_registry

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).parent.parent / "plugins"
_loaded_plugins: dict[str, dict] = {}  # name -> {manifest, handler_module, idle_module, ...}

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

        if not manifest.get("enabled", True):
            logger.info(f"[plugin_loader] Plugin '{manifest['name']}' is disabled, skipping")
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
            data_dir = Path("data/plugins") / name
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / db_file
            if hasattr(storage_mod, "init"):
                storage_mod.init(str(db_path))
                logger.info(f"[plugin_loader] Initialized storage: data/plugins/{name}/{db_file}")
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


def enable_plugin(name: str) -> bool:
    """Enable a plugin by updating its manifest."""
    plugin_dir = _PLUGINS_DIR / name
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["enabled"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if name not in _loaded_plugins:
        _load_plugin(plugin_dir, manifest)
    return True


def disable_plugin(name: str) -> bool:
    """Disable a plugin by updating its manifest."""
    plugin_dir = _PLUGINS_DIR / name
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["enabled"] = False
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # Unregister tools
    if name in _loaded_plugins:
        for tool_def in _loaded_plugins[name]["manifest"].get("tools", []):
            tool_registry.unregister(tool_def["name"])
        del _loaded_plugins[name]
    return True


def get_plugin_triggers() -> list[tuple[str, list[str], bool]]:
    """Return (regex_pattern, tool_names, requires_intent) for each plugin.

    Plugins require intent prefix. Main features (registered elsewhere) do not.
    """
    triggers = []
    for name, entry in _loaded_plugins.items():
        pattern = entry["manifest"].get("triggers")
        if pattern:
            tool_names = [t["name"] for t in entry["manifest"].get("tools", [])]
            triggers.append((pattern, tool_names, True))  # True = requires intent prefix
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


def get_plugin_background_tasks() -> list[tuple[str, Callable, bool]]:
    """Collect background task functions from all enabled plugins.

    Returns: list of (plugin_name, async_function, run_during_sleep)
    Unlike background_checks (which return data), tasks handle their own logic
    (notifications, message queue, etc.)
    """
    tasks = []
    for name, entry in _loaded_plugins.items():
        bt_config = entry["manifest"].get("background_task", {})
        if bt_config.get("enabled"):
            fn_name = bt_config.get("function")
            run_during_sleep = bt_config.get("run_during_sleep", False)
            handler_mod = entry.get("handler")
            if handler_mod and fn_name and hasattr(handler_mod, fn_name):
                tasks.append((name, getattr(handler_mod, fn_name), run_during_sleep))
    return tasks


async def handle_callback(plugin_name: str, action: str, item_id: str) -> dict | None:
    """Handle a Telegram inline button callback for a plugin."""
    entry = _loaded_plugins.get(plugin_name)
    if not entry:
        return None
    handler_mod = entry.get("handler")
    if handler_mod and hasattr(handler_mod, "handle_callback"):
        return await handler_mod.handle_callback(action, item_id)
    return None
