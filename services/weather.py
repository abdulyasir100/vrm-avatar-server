"""Weather service — fetches current weather from Open-Meteo (free, no API key).

Caches results for 30 minutes to avoid hammering the API.
Location defaults to Depok, Indonesia (configurable in config.py).
"""

import time
import logging
import httpx

logger = logging.getLogger(__name__)

_cache: dict | None = None
_cache_time: float = 0.0
_CACHE_TTL = 1800  # 30 minutes

_WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

_WMO_CONDITION = {
    0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy",
    45: "foggy", 48: "foggy",
    51: "drizzle", 53: "drizzle", 55: "drizzle",
    61: "rainy", 63: "rainy", 65: "rainy",
    71: "snowy", 73: "snowy", 75: "snowy",
    80: "rainy", 81: "rainy", 82: "rainy",
    95: "stormy", 96: "stormy", 99: "stormy",
}


async def fetch_weather(lat: float, lon: float) -> dict | None:
    """Fetch current weather + daily forecast from Open-Meteo.

    Returns parsed dict or None on failure.
    """
    global _cache, _cache_time

    if _cache and (time.time() - _cache_time) < _CACHE_TTL:
        return _cache

    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,apparent_temperature",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
            "timezone": "Asia/Jakarta",
            "forecast_days": 3,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        current = data["current"]
        daily = data["daily"]
        code = current["weather_code"]

        result = {
            "current": {
                "temperature": current["temperature_2m"],
                "feels_like": current["apparent_temperature"],
                "humidity": current["relative_humidity_2m"],
                "wind_speed": current["wind_speed_10m"],
                "weather_code": code,
                "description": _WMO_CODES.get(code, "Unknown"),
                "condition": _WMO_CONDITION.get(code, "clear"),
            },
            "forecast": [],
        }

        for i in range(len(daily["time"])):
            result["forecast"].append({
                "date": daily["time"][i],
                "code": daily["weather_code"][i],
                "description": _WMO_CODES.get(daily["weather_code"][i], "Unknown"),
                "condition": _WMO_CONDITION.get(daily["weather_code"][i], "clear"),
                "temp_max": daily["temperature_2m_max"][i],
                "temp_min": daily["temperature_2m_min"][i],
                "precipitation": daily["precipitation_sum"][i],
                "rain_chance": daily["precipitation_probability_max"][i],
            })

        _cache = result
        _cache_time = time.time()
        logger.info(f"[weather] Fetched: {result['current']['description']}, {result['current']['temperature']}°C")
        return result

    except Exception as e:
        logger.error(f"[weather] Fetch failed: {e}")
        return _cache


def format_summary(data: dict) -> str:
    """Format weather data as natural speech for TTS."""
    c = data["current"]
    summary = f"Right now it's {c['description'].lower()}, {c['temperature']} degrees"
    if abs(c['temperature'] - c['feels_like']) >= 2:
        summary += f", feels like {c['feels_like']}"
    summary += "."

    if data["forecast"]:
        f = data["forecast"][0]
        summary += f" Tomorrow: {f['description'].lower()}, {f['temp_min']} to {f['temp_max']} degrees"
        if f['rain_chance'] > 30:
            summary += f", {f['rain_chance']}% chance of rain"
        summary += "."

    return summary
