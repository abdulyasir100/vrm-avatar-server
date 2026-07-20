"""Extensible tool registry for LLM-triggered actions.

Supports two modes:
  - Text-tag mode: LLM outputs [TOOL:name:arg] in response text (local models)
  - Function-calling mode: tools passed as OpenAI function schemas (cloud models)
"""

import logging
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# Handler signature: async (arg: str, context: dict) -> dict
# Returns: {"ok": bool, "result": str, "side_effects": list[dict]}
ToolHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

_registry: dict[str, ToolHandler] = {}

# OpenAI-compatible function schemas for native tool calling
_schemas: dict[str, dict] = {}

# Main tools — Unity-connected or core system (memory, costume, gacha, etc.).
# Plugin tools (calorie, money, todo, weather, etc.) are NOT in this set.
# Used by TOOL_CALL_MODE=semi_normal to restrict tools to main features only.
MAIN_TOOLS: set[str] = {
    "change_costume",
    "save_memory", "update_memory", "delete_memory",
    "open_gacha", "open_roulette", "spin_roulette", "give_thr",
}


def is_main_tool(name: str) -> bool:
    return name in MAIN_TOOLS


def register(name: str, handler: ToolHandler, schema: dict | None = None):
    """Register a tool handler by name, with optional OpenAI function schema."""
    _registry[name] = handler
    if schema:
        _schemas[name] = schema
    logger.info(f"[tools] Registered tool: {name}")


def unregister(name: str):
    """Remove a tool handler by name."""
    _registry.pop(name, None)
    _schemas.pop(name, None)
    logger.info(f"[tools] Unregistered tool: {name}")


def get(name: str) -> ToolHandler | None:
    """Look up a tool handler by name. Returns None if not found."""
    return _registry.get(name)


def list_tools() -> list[str]:
    """Return names of all registered tools."""
    return list(_registry.keys())


def get_openai_tools() -> list[dict]:
    """Return all tool schemas in OpenAI function-calling format."""
    return [
        {"type": "function", "function": schema}
        for schema in _schemas.values()
    ]
