"""Example plugin handler — copy and modify for new plugins.

Key patterns:
- set_storage(): called by plugin_loader after DB init
- handle_<tool_name>(): tool call handlers (must match manifest tool names)
- cmd_<name>(): Telegram command handlers (must match manifest telegram_commands)
- handle_callback(): inline button callback handler
- check_something(): background_check function (returns data)
- background_tick(): background_task function (runs own logic)
- scheduled_daily(): scheduled_action function (cron-like)
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Storage module — injected by plugin_loader after init
_storage = None


def set_storage(storage_module):
    """Called by plugin_loader after initializing storage."""
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Plugin storage not initialized")
    return _storage


# --- Tool Handlers ---
# Function name MUST be "handle_" + tool name from manifest

async def handle_example_tool(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Handle the example_tool call.

    Returns: {"ok": bool, "result": str, "side_effects": list}
    - result: text shown to LLM for natural language formatting
    - side_effects: optional WebSocket broadcasts or inline keyboards
    """
    storage = _get_storage()
    # ... do work with storage ...
    return {"ok": True, "result": f"Example result for: {arg}", "side_effects": []}


# --- Telegram Command Handlers ---
# Function name must match manifest telegram_commands[].function

async def cmd_list(args: str = "") -> dict:
    """Handle /p.example command. Returns text + optional inline keyboard."""
    return {
        "text": "Example plugin — no data yet.",
        "inline_keyboard": None,  # or list of button rows
    }


async def cmd_add(args: str = "") -> dict:
    """Handle /p.example.add command."""
    if not args:
        return {"text": "Usage: /p.example.add <item>", "inline_keyboard": None}
    # ... add item ...
    return {"text": f"Added: {args}", "inline_keyboard": None}


# --- Callback Handler ---
# For inline keyboard button presses

async def handle_callback(action: str, item_id: str) -> dict | None:
    """Handle Telegram inline button callback.

    Callback data format: plugin:example:action:item_id
    Returns: {"message": str, "refresh": bool}
    """
    storage = _get_storage()
    if action == "delete":
        # ... delete item_id ...
        return {"message": "Deleted.", "refresh": True}
    return None


# --- Background Check ---
# Returns data for the background loop to process

async def check_something():
    """Background check — called every interval_seconds when awake.

    MUST return a list of dicts or None. Never return a bare dict — it will
    be iterated by key and crash the background loop.

    Two formats:
      Prompt-based (most plugins):
        [{"type": "my_reminder", "prompt": "Nag the user about X. One sentence."}]
      Reminder-based (todo-style):
        [{"task_id": "...", "title": "...", "minutes_until_due": 5}]
    """
    storage = _get_storage()
    # Return list of items that need attention, or empty list
    return []


# --- Background Task ---
# Runs its own logic (can send notifications, enqueue speech)

async def background_tick():
    """Background task — called every interval_seconds. Can run during sleep if configured."""
    # Import what you need:
    # from services import ntfy, sleep
    # from services.message_queue import QueueItem, enqueue
    pass


# --- Scheduled Action ---
# Called at a specific time daily

async def scheduled_daily():
    """Called at the configured time. Return a prompt string for LLM to speak, or None."""
    return None  # Return a prompt string like "Tell the user about X. One sentence."
