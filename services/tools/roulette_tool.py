"""Roulette tool — LLM can open a roulette wheel and spin it."""

import logging
import time
from typing import Any
import httpx
from services import tool_registry
from services.ws_manager import manager
import config

logger = logging.getLogger(__name__)

LUCKY_DRAW_URL = getattr(config, "LUCKY_DRAW_URL", "http://localhost:8804")

# Module-level state for two-step roulette
_roulette_active = False
_roulette_timestamp = 0.0
_ROULETTE_TIMEOUT = 120  # seconds


async def _api(method: str, path: str, json: dict | None = None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, f"{LUCKY_DRAW_URL}{path}", json=json)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"[roulette_tool] API error: {method} {path} — {e}")
        return None


def check_roulette_timeout():
    """Called from background loop to auto-reset stale roulette state."""
    global _roulette_active
    if _roulette_active and (time.time() - _roulette_timestamp) > _ROULETTE_TIMEOUT:
        _roulette_active = False
        logger.info("[roulette_tool] Roulette timed out, state reset")


async def handle_open_roulette(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Open the roulette wheel on the tablet, or pick winner immediately if no tablet."""
    global _roulette_active, _roulette_timestamp

    participants = await _api("GET", "/roulette/participants")
    if participants is None:
        return {"ok": False, "result": "Lucky Draw service is offline~", "side_effects": []}
    if not participants:
        return {"ok": False, "result": "No participants in the roulette! Add some first.", "side_effects": []}

    names = [p["name"] for p in participants]

    if manager.client_count > 0:
        _roulette_active = True
        _roulette_timestamp = time.time()
        side_effect = {
            "type": "roulette",
            "phase": "open",
            "participants_csv": ",".join(names),
        }
        return {
            "ok": True,
            "result": "",
            "side_effects": [side_effect],
            "skip_tts": True,
        }
    else:
        # No tablet — spin immediately
        result = await _api("POST", "/roulette/spin")
        if result is None:
            return {"ok": False, "result": "Spin failed!", "side_effects": []}
        winner = result["winner"]["name"]
        return {
            "ok": True,
            "result": f"And the winner is... {winner}! Congratulations~",
            "side_effects": [],
        }


async def handle_spin_roulette(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Spin the roulette wheel (requires open_roulette first on tablet)."""
    global _roulette_active

    if not _roulette_active:
        return {"ok": False, "result": "The roulette isn't open yet! Open it first~", "side_effects": []}

    result = await _api("POST", "/roulette/spin")
    if result is None:
        _roulette_active = False
        return {"ok": False, "result": "Spin failed!", "side_effects": []}

    winner = result["winner"]["name"]
    _roulette_active = False

    if manager.client_count > 0:
        side_effect = {
            "type": "roulette",
            "phase": "spin",
            "winner": winner,
        }
        return {
            "ok": True,
            "result": "",
            "side_effects": [side_effect],
            "skip_tts": True,
        }
    else:
        return {
            "ok": True,
            "result": f"And the winner is... {winner}! Congratulations~",
            "side_effects": [],
        }


tool_registry.register("open_roulette", handle_open_roulette, schema={
    "name": "open_roulette",
    "description": "Open the roulette wheel to pick a random winner from participants. Use when the user wants to do a roulette, lucky draw, or spin the wheel.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})

tool_registry.register("spin_roulette", handle_spin_roulette, schema={
    "name": "spin_roulette",
    "description": "Spin the roulette wheel after it's been opened. Use when the user says 'spin', 'roll it', or 'go' after the roulette is open.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
