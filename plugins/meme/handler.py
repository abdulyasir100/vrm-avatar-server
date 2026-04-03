"""Meme plugin — Indonesian political memes via meme-service."""

import logging
import httpx
from typing import Any
import config

logger = logging.getLogger(__name__)
MEME_SERVICE_URL = getattr(config, "MEME_SERVICE_URL", "http://localhost:8807")


async def handle_get_political_meme(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    name = arg.strip() if arg else ""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            if name:
                resp = await client.get(f"{MEME_SERVICE_URL}/meme", params={"name": name})
            else:
                resp = await client.get(f"{MEME_SERVICE_URL}/meme/random")
            data = resp.json()
            if not data.get("ok"):
                return {"ok": False, "result": data.get("error", "Meme service unavailable"), "side_effects": []}
            return {"ok": True, "result": data["meme"], "side_effects": []}
    except Exception as e:
        logger.error(f"[meme] Failed: {e}")
        return {"ok": False, "result": "Meme service is offline", "side_effects": []}


async def cmd_meme(args: str = "") -> dict:
    result = await handle_get_political_meme(args, {})
    if result["ok"]:
        return {"text": f"🇮🇩 {result['result']}", "inline_keyboard": None}
    return {"text": result["result"], "inline_keyboard": None}
