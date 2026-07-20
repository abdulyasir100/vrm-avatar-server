"""Costume change tool — switches VRM avatar outfit via WebSocket."""

import logging
from typing import Any
from services import tool_registry, costume_registry

logger = logging.getLogger(__name__)


def get_current_costume() -> str:
    return costume_registry.get_current()


def set_current_costume(costume_id: str):
    costume_registry.set_current(costume_id)


async def handle_change_costume(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Change the VRM avatar costume.

    Args:
        arg: costume query — exact ID ("maid"), tag ("favorite", "comfy"),
             or special value ("random")
        context: chat context dict

    Returns:
        {"ok": bool, "result": str, "side_effects": list[dict]}
    """
    query = arg.strip().lower()
    current = costume_registry.get_current()

    # Resolve query to costume ID via tag matching
    costume_id = costume_registry.resolve(query)

    if not costume_id:
        available = sorted(costume_registry.get_enabled().keys())
        tags = costume_registry.list_tags()
        return {
            "ok": False,
            "result": f"No costume called {query}. I have {', '.join(available)}.",
            "side_effects": [],
        }

    # Already wearing it
    if costume_id == current:
        return {
            "ok": True,
            "result": f"Already wearing the {costume_id} outfit.",
            "side_effects": [],
        }

    costume_registry.set_current(costume_id)
    logger.info(f"[costume_tool] Costume change: {current} -> {costume_id}")

    return {
        "ok": True,
        "result": f"Switched to the {costume_id} outfit.",
        "side_effects": [
            {"type": "costume", "costume_id": costume_id},
        ],
    }


# Register on import
tool_registry.register("change_costume", handle_change_costume, schema={
    "name": "change_costume",
    "description": "Change the VRM avatar's outfit/costume. Use when the user asks to change, switch, or wear a different outfit.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Costume ID or query. Options: 'maid', 'casual', 'random', or tags like 'comfy', 'favorite'.",
            }
        },
        "required": ["arg"],
    },
})
