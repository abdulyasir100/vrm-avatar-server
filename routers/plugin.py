"""Plugin API — list plugins, execute commands, handle callbacks."""

import logging
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Any
from services import plugin_loader
from services.auth import require_key

logger = logging.getLogger(__name__)
# Dedicated chat-log channel so dashboard_stats can grep /p.* invocations
# (the regular logger goes to all.log only).
chat_logger = logging.getLogger("chat")
# Plugin commands act on the user's behalf (smart home, clock-in, passwords), so remote
# callers need the admin key. Same-host services (the game backend) call this too.
router = APIRouter(prefix="/plugin", dependencies=[Depends(require_key("ADMIN_KEY", "X-Admin-Key", trust_loopback=True))])


@router.get("/list")
async def list_plugins():
    """Return all plugin manifests for dynamic Telegram help/commands."""
    plugins = plugin_loader.get_enabled_plugins()
    result = []
    for name, entry in plugins.items():
        manifest = entry["manifest"]
        result.append({
            "name": manifest["name"],
            "description": manifest.get("description", ""),
            "enabled": manifest.get("enabled", True),
            "commands": manifest.get("telegram_commands", []),
            "settings": manifest.get("settings_schema", {}),
        })
    return {"plugins": result}


@router.get("/guide")
async def plugin_guide():
    """Auto-generate trigger examples from plugin prompt.md files.

    Extracts lines starting with '- Example:' and strips [TOOL:...] tags.
    Returns structured data for Telegram /guide display.
    """
    import re
    plugins = plugin_loader.get_enabled_plugins()
    sections = []
    for name in sorted(plugins):
        entry = plugins[name]
        doc = entry.get("prompt_doc", "").strip()
        if not doc:
            continue

        # Extract example lines
        examples = []
        for line in doc.split("\n"):
            line = line.strip()
            if not line.lower().startswith("- example:"):
                continue
            # Strip "- Example: " prefix
            example = re.sub(r"^-\s*Example:\s*", "", line, flags=re.IGNORECASE)
            # Strip [TOOL:...] tags
            example = re.sub(r"\s*→\s*\[TOOL:[^\]]*\]", "", example)
            # Strip surrounding quotes
            example = example.strip().strip('"').strip("'")
            if example:
                examples.append(example)

        if examples:
            sections.append({"name": name, "examples": examples})

    return {"sections": sections}


# ─── management (settings page) ─────────────────────────────────────────────


def _row(name: str) -> dict:
    row = next((p for p in plugin_loader.list_all() if p["name"] == name), None)
    if row is None:
        raise HTTPException(404, f"Plugin '{name}' not found.")
    return row


@router.get("/all")
async def all_plugins():
    """Every plugin on disk with this instance's on/off state and settings rows."""
    return {"plugins": plugin_loader.list_all()}


@router.post("/{name}/enable")
async def enable(name: str):
    _row(name)
    reason = plugin_loader.locked_reason(name)
    if reason:
        raise HTTPException(409, f"{name} is locked: {reason}")
    try:
        ok = plugin_loader.enable_plugin(name)
    except Exception as e:
        logger.error(f"[plugin] enable {name} failed: {e!r}")
        raise HTTPException(500, f"{name} failed to load: {e}")
    if not ok:
        raise HTTPException(404, f"Plugin '{name}' not found.")
    # Toggling only updates tool_registry live; plugin trigger routes and the LLM's
    # tool docs/SDK tool list are resolved once at startup, so chat tools need a restart.
    return {"ok": True, "plugin": _row(name), "restart_required": True}


@router.post("/{name}/disable")
async def disable(name: str):
    _row(name)
    ok = await plugin_loader.disable_plugin(name)
    if not ok:
        raise HTTPException(404, f"Plugin '{name}' not found.")
    return {"ok": True, "plugin": _row(name), "restart_required": True}


@router.get("/{name}/settings")
async def get_settings(name: str):
    _row(name)
    return {"settings": plugin_loader.get_settings(name)}


@router.put("/{name}/settings")
async def put_settings(name: str, values: dict[str, Any] = Body(...)):
    _row(name)
    if name not in plugin_loader.get_enabled_plugins():
        raise HTTPException(409, f"Enable {name} before changing its settings.")
    schema = {row["key"] for row in plugin_loader.get_settings(name)}
    unknown = sorted(set(values) - schema)
    if unknown:
        raise HTTPException(400, f"Unknown setting(s): {', '.join(unknown)}")
    # Validate the whole batch before writing any of it.
    try:
        for key, value in values.items():
            plugin_loader.validate_setting(name, key, value)
        for key, value in values.items():
            plugin_loader.set_setting(name, key, value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"settings": plugin_loader.get_settings(name)}


@router.delete("/{name}/settings/{key}")
async def reset_setting(name: str, key: str):
    _row(name)
    try:
        plugin_loader.reset_setting(name, key)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(409, str(e))
    return {"settings": plugin_loader.get_settings(name)}


class PluginCommandRequest(BaseModel):
    plugin: str
    command: str
    args: Optional[str] = ""


class PluginCommandResponse(BaseModel):
    ok: bool
    text: str = ""
    inline_keyboard: Optional[list[list[dict[str, str]]]] = None


@router.post("/command", response_model=PluginCommandResponse)
async def plugin_command(req: PluginCommandRequest):
    """Execute a Telegram command for a plugin (e.g. /p.tasks → plugin=todo, command=tasks)."""
    entry = plugin_loader.get_plugin(req.plugin)
    if not entry:
        return PluginCommandResponse(ok=False, text=f"Plugin '{req.plugin}' not found.")

    handler = entry.get("handler")
    if not handler:
        return PluginCommandResponse(ok=False, text=f"Plugin '{req.plugin}' has no handler.")

    # Find the matching command
    manifest = entry["manifest"]
    cmd_config = None
    for cmd in manifest.get("telegram_commands", []):
        if cmd["command"] == req.command or cmd["command"].split(".")[-1] == req.command:
            cmd_config = cmd
            break

    if not cmd_config:
        return PluginCommandResponse(ok=False, text=f"Unknown command: {req.command}")

    fn_name = cmd_config["function"]
    fn = getattr(handler, fn_name, None)
    if not fn:
        return PluginCommandResponse(ok=False, text=f"Handler '{fn_name}' not implemented.")

    try:
        result = await fn(req.args) if callable(fn) else None
        if result is None:
            return PluginCommandResponse(ok=False, text="Command returned nothing.")
        # Dashboard tally: emit a chat-log line per successful command so
        # /p.* invocations are counted in plugin_commands. Use the matched
        # manifest command (full dotted form) so it lines up with the seeded
        # zero-counts in dashboard_stats.plugin_commands().
        chat_logger.info(f"[plugin_cmd] /p.{cmd_config['command']}")
        return PluginCommandResponse(
            ok=True,
            text=result.get("text", ""),
            inline_keyboard=result.get("inline_keyboard"),
        )
    except Exception as e:
        logger.error(f"[plugin] Command {req.plugin}/{req.command} failed: {e}")
        return PluginCommandResponse(ok=False, text=f"Error: {e}")


class PluginCallbackRequest(BaseModel):
    plugin: str
    action: str
    item_id: str


@router.post("/callback")
async def plugin_callback(req: PluginCallbackRequest):
    """Handle Telegram inline button callback."""
    result = await plugin_loader.handle_callback(req.plugin, req.action, req.item_id)
    if not result:
        return {"ok": False, "message": "Callback failed"}

    # If refresh requested, re-run the appropriate command to get updated view
    updated_list = None
    if result.get("refresh"):
        refresh_fn_name = result.get("refresh_function")
        entry = plugin_loader.get_plugin(req.plugin)
        if entry and entry.get("handler"):
            handler = entry["handler"]
            commands = entry["manifest"].get("telegram_commands", [])
            # Use explicit refresh_function, or fall back to first command
            fn = None
            if refresh_fn_name:
                fn = getattr(handler, refresh_fn_name, None)
            if not fn and commands:
                fn = getattr(handler, commands[0]["function"], None)
            if fn:
                try:
                    updated_list = await fn("")
                except Exception:
                    pass

    return {
        "ok": True,
        "message": result.get("message", "Done"),
        "updated": updated_list,
    }
