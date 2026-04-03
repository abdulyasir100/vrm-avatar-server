"""THR (Tunjangan Hari Raya) tool — envelope shuffle pick game."""

import logging
import random
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
        logger.error(f"[thr_tool] API error: {method} {path} — {e}")
        return None


def _expand_items(items: list[dict], target: int = 10) -> list[dict]:
    """Expand items by quantity into individual slots, then shuffle.

    Each item's quantity determines how many envelope slots it fills.
    If total < target, pad with random items. If total > target, trim.
    """
    if not items:
        return []
    result = []
    for item in items:
        qty = max(item.get("quantity", 1), 1)
        for _ in range(qty):
            result.append(item)
    # Pad if under target
    while len(result) < target:
        result.append(random.choice(items))
    random.shuffle(result)
    return result[:target]


async def handle_give_thr(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Give a THR envelope — shuffle pick game on tablet, or random pick via text."""
    items = await _api("GET", "/thr/items")
    if items is None:
        return {"ok": False, "result": "Lucky Draw service is offline~", "side_effects": []}
    if not items:
        return {"ok": False, "result": "No THR items configured! Add some first.", "side_effects": []}

    if manager.client_count > 0:
        padded = _expand_items(items)
        # Build JSON string for items since JsonUtility can't handle arrays directly
        import json
        items_json = json.dumps([{
            "id": i["id"],
            "name": i["name"],
            "image_url": i.get("image_url", ""),
            "quantity": i.get("quantity", 1),
        } for i in padded])

        side_effect = {
            "type": "thr",
            "phase": "open",
            "items_json": items_json,
        }
        return {
            "ok": True,
            "result": "",
            "side_effects": [side_effect],
            "skip_reply": True,
        }
    else:
        result = await _api("POST", "/thr/open")
        if result is None:
            return {"ok": False, "result": "Failed to open THR!", "side_effects": []}
        item = result["item"]
        return {
            "ok": True,
            "result": f"You got: {item['name']}! Happy Hari Raya~",
            "side_effects": [],
        }


async def handle_thr_result(item_id: str) -> None:
    """Record a THR pick from the tablet (called from ws.py)."""
    result = await _api("POST", "/thr/record", json={"item_id": item_id})
    if result:
        logger.info(f"[thr_tool] Recorded THR pick: {result['item']['name']}")
    else:
        logger.warning(f"[thr_tool] Failed to record THR pick for item_id={item_id}")


tool_registry.register("give_thr", handle_give_thr, schema={
    "name": "give_thr",
    "description": "Give a THR (Tunjangan Hari Raya) envelope! A shuffle pick game where the user picks one envelope from many. Use when the user asks for THR, angpao, or holiday envelope.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
