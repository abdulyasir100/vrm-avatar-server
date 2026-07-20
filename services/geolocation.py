"""IP-based geolocation with config fallback.

Auto-detects location once on first call via ip-api.com,
then caches for the server's lifetime. Falls back to config values.
"""

import logging
import httpx
import config

logger = logging.getLogger(__name__)

_geo: dict | None = None


async def get_location() -> dict:
    """Return {"lat": float, "lon": float, "city": str, "country": str}.

    First call hits ip-api.com; subsequent calls return cached result.
    Falls back to WEATHER_LAT/LON from config if geolocation fails.
    """
    global _geo
    if _geo is not None:
        return _geo

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "http://ip-api.com/json/",
                params={"fields": "status,lat,lon,city,country"},
            )
            data = resp.json()
            if data.get("status") == "success":
                _geo = {
                    "lat": data["lat"],
                    "lon": data["lon"],
                    "city": data.get("city", ""),
                    "country": data.get("country", config.LOCALE_COUNTRY),
                }
                logger.info(
                    f"[geo] Located via IP: {_geo['city']}, {_geo['country']} "
                    f"({_geo['lat']}, {_geo['lon']})"
                )
                return _geo
    except Exception as e:
        logger.warning(f"[geo] IP geolocation failed: {e}")

    _geo = {
        "lat": config.WEATHER_LAT,
        "lon": config.WEATHER_LON,
        "city": config.WEATHER_LOCATION_NAME.split(",")[0].strip(),
        "country": config.LOCALE_COUNTRY,
    }
    logger.info(f"[geo] Using config fallback: {_geo['city']} ({_geo['lat']}, {_geo['lon']})")
    return _geo


def get_cached_location() -> dict | None:
    """Return cached location without making any network calls. None if not yet fetched."""
    return _geo
