"""Plugin API — list plugins, execute commands, handle callbacks."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Any
from services import plugin_loader

logger = logging.getLogger(__name__)
# Dedicated chat-log channel so dashboard_stats can grep /p.* invocations
# (the regular logger goes to all.log only).
chat_logger = logging.getLogger("chat")
router = APIRouter(prefix="/plugin")


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
