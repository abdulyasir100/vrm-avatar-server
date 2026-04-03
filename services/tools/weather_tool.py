"""Weather tool — LLM can check current weather and forecast."""

import logging
from typing import Any
from services import tool_registry, weather
from services.geolocation import get_location

logger = logging.getLogger(__name__)


async def handle_check_weather(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check current weather and 3-day forecast."""
    geo = await get_location()
    data = await weather.fetch_weather(geo["lat"], geo["lon"])

    if data is None:
        return {"ok": False, "result": "Weather service unavailable", "side_effects": []}

    summary = weather.format_summary(data)
    return {"ok": True, "result": summary, "side_effects": []}


# Register on import
tool_registry.register("check_weather", handle_check_weather, schema={
    "name": "check_weather",
    "description": "Check current weather and 3-day forecast for the user's location. Use when the user asks about weather, temperature, rain, or if they should bring an umbrella.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
