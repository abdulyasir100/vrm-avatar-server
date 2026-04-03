"""Political meme tool — Suisei responds with Indonesian political memes."""

import logging
import httpx
from typing import Any
from services import tool_registry
import config

logger = logging.getLogger(__name__)

MEME_SERVICE_URL = getattr(config, "MEME_SERVICE_URL", "http://localhost:8807")


async def handle_get_meme(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Fetch a random political meme for the mentioned figure."""
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

            meme = data["meme"]
            return {
                "ok": True,
                "result": meme,
                "side_effects": [],
            }
    except Exception as e:
        logger.error(f"[meme_tool] Failed: {e}")
        return {"ok": False, "result": "Meme service is offline", "side_effects": []}


tool_registry.register("get_political_meme", handle_get_meme, schema={
    "name": "get_political_meme",
    "description": "Get a funny Indonesian political meme or catchphrase. Use when the user mentions Indonesian politicians like Jokowi, Prabowo, Gibran, Megawati, Luhut, Anies, or DPR/parliament. Respond with the meme in a playful way.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "The politician name (e.g. 'jokowi', 'prabowo', 'gibran', 'megawati', 'luhut', 'anies', 'dpr'). Leave empty for random.",
            },
        },
        "required": [],
    },
})
