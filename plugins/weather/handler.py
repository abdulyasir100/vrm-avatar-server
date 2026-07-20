"""Weather plugin — check weather via Open-Meteo."""

import logging
from typing import Any
from services import weather
from services.geolocation import get_location

logger = logging.getLogger(__name__)


async def handle_check_weather(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    geo = await get_location()
    data = await weather.fetch_weather(geo["lat"], geo["lon"])
    if data is None:
        return {"ok": False, "result": "Weather service unavailable", "side_effects": []}
    summary = weather.format_summary(data)
    return {"ok": True, "result": summary, "side_effects": []}


async def cmd_weather(args: str = "") -> dict:
    geo = await get_location()
    data = await weather.fetch_weather(geo["lat"], geo["lon"])
    if data is None:
        return {"text": "Weather service unavailable.", "inline_keyboard": None}
    c = data["current"]
    f = data.get("forecast", [])
    lines = [
        f"🌤 Weather Now",
        f"{c['description']}, {c['temperature']}°C (feels {c['feels_like']}°C)",
        f"Humidity: {c.get('humidity', '?')}% | Wind: {c.get('wind_speed', '?')} km/h",
    ]
    for day in f[:3]:
        lines.append(f"  {day['date']}: {day['description']} {day['temp_min']}–{day['temp_max']}°C")
    return {"text": "\n".join(lines), "inline_keyboard": None}
