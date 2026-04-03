"""Memory tools — LLM can save, update, and delete core memories."""

import logging
from typing import Any
from services import memory, tool_registry

logger = logging.getLogger(__name__)

# Valid categories for core memories
_VALID_CATEGORIES = {"fact", "preference", "relationship", "event"}


async def handle_save_memory(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Save a new core memory.

    Arg format: category|content
    Example: fact|User's favorite color is blue
    """
    parts = arg.split("|", 1)
    if len(parts) != 2 or not parts[1].strip():
        return {
            "ok": False,
            "result": f"Invalid format. Expected: category|content. Got: {arg}",
            "side_effects": [],
        }

    category = parts[0].strip().lower()
    content = parts[1].strip()

    if category not in _VALID_CATEGORIES:
        return {
            "ok": False,
            "result": f"Unknown category '{category}'. Valid: {sorted(_VALID_CATEGORIES)}",
            "side_effects": [],
        }

    mem_id = memory.add_core_memory(category, content, source="llm")
    logger.info(f"[memory_tool] Saved core memory #{mem_id} [{category}]: {content}")

    return {
        "ok": True,
        "result": f"Got it, I'll remember that.",
        "side_effects": [],
    }


async def handle_update_memory(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Update an existing core memory by ID.

    Arg format: id|new content
    Example: 3|User's favorite color is now red
    """
    parts = arg.split("|", 1)
    if len(parts) != 2 or not parts[1].strip():
        return {
            "ok": False,
            "result": f"Invalid format. Expected: id|new_content. Got: {arg}",
            "side_effects": [],
        }

    try:
        mem_id = int(parts[0].strip())
    except ValueError:
        return {
            "ok": False,
            "result": f"Invalid memory ID: {parts[0]}",
            "side_effects": [],
        }

    new_content = parts[1].strip()
    updated = memory.update_core_memory(mem_id, new_content)

    if not updated:
        return {
            "ok": False,
            "result": f"Memory #{mem_id} not found",
            "side_effects": [],
        }

    logger.info(f"[memory_tool] Updated core memory #{mem_id}: {new_content}")
    return {
        "ok": True,
        "result": f"Updated that memory.",
        "side_effects": [],
    }


async def handle_delete_memory(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Delete a core memory by ID.

    Arg format: id
    Example: 3
    """
    try:
        mem_id = int(arg.strip())
    except ValueError:
        return {
            "ok": False,
            "result": f"Invalid memory ID: {arg}",
            "side_effects": [],
        }

    deleted = memory.delete_core_memory(mem_id)

    if not deleted:
        return {
            "ok": False,
            "result": f"Memory #{mem_id} not found",
            "side_effects": [],
        }

    logger.info(f"[memory_tool] Deleted core memory #{mem_id}")
    return {
        "ok": True,
        "result": f"Forgot that one.",
        "side_effects": [],
    }


# Register on import
tool_registry.register("save_memory", handle_save_memory, schema={
    "name": "save_memory",
    "description": "Save an important fact, preference, or event to permanent memory. Use proactively when the user shares something worth remembering long-term.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Pipe-separated: category|content. Categories: 'fact', 'preference', 'relationship', 'event'. Example: 'fact|User likes blue', 'preference|User prefers morning calls'",
            }
        },
        "required": ["arg"],
    },
})
tool_registry.register("update_memory", handle_update_memory, schema={
    "name": "update_memory",
    "description": "Update an existing core memory by its ID. Use when information has changed.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Pipe-separated: id|new_content. Example: '3|User now prefers red over blue'",
            }
        },
        "required": ["arg"],
    },
})
tool_registry.register("delete_memory", handle_delete_memory, schema={
    "name": "delete_memory",
    "description": "Delete a core memory by its ID. Use when a memory is no longer relevant.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Memory ID to delete. Example: '3'",
            }
        },
        "required": ["arg"],
    },
})
