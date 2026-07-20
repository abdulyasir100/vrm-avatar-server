"""Gacha tool — LLM can trigger a gacha pull with tablet animation."""

import logging
from typing import Any
import httpx
from services import tool_registry
from services.ws_manager import manager
import config

logger = logging.getLogger(__name__)

LUCKY_DRAW_URL = getattr(config, "LUCKY_DRAW_URL", "http://localhost:8804")


async def _api(method: str, path: str, json: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, f"{LUCKY_DRAW_URL}{path}", json=json)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"[gacha_tool] API error: {method} {path} — {e}")
        return None


async def handle_open_gacha(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Pull a random gacha item. If tablet is connected, show animation."""
    result = await _api("POST", "/gacha/pull")
    if result is None:
        return {"ok": False, "result": "Lucky Draw service is offline~", "side_effects": []}

    item = result.get("item", {})
    name = item.get("name", "???")
    rarity = item.get("rarity", "N")
    image_url = item.get("image_url", "")

    if manager.client_count > 0:
        side_effect = {
            "type": "gacha",
            "phase": "open",
            "item_name": name,
            "rarity": rarity,
            "image_url": image_url,
        }
        return {
            "ok": True,
            "result": "",
            "side_effects": [side_effect],
        }
    else:
        rarity_stars = {"SSR": "!!!", "SR": "!!", "R": "!", "N": ""}
        return {
            "ok": True,
            "result": f"You got [{rarity}]{rarity_stars.get(rarity, '')} — {name}!",
            "side_effects": [],
        }


tool_registry.register("open_gacha", handle_open_gacha, schema={
    "name": "open_gacha",
    "description": "Do a gacha pull! Draws a random item from the gacha pool. Use when the user wants to do a gacha, pull, draw, or summon.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
