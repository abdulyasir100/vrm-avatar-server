"""Prayer times — Aladhan API with daily cache, IP geolocation, and reminder tracking.

Replaces prayer task generation in task-manager. Standalone, no external dependencies
beyond httpx (already installed).

Location is auto-detected via IP geolocation on first fetch, with config values as fallback.
"""

import logging
from datetime import datetime, timezone, timedelta

from utils import time as ltime
import httpx
from services.geolocation import get_location

logger = logging.getLogger(__name__)

_cache: dict[str, dict] = {}

_sent_reminders: set[str] = set()

PRAYER_NAMES = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]

REMINDER_OFFSETS = [5]


def _today_wib() -> str:
    return ltime.now().strftime("%Y-%m-%d")


def _now_wib() -> datetime:
    return ltime.now()


async def fetch_prayer_times() -> dict | None:
    """Fetch today's prayer times from Aladhan API. Cached per day."""
    today = _today_wib()

    if today in _cache:
        return _cache[today]

    geo = await get_location()

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                "http://api.aladhan.com/v1/timings",
                params={
                    "latitude": geo["lat"],
                    "longitude": geo["lon"],
                    "method": 20,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"[prayer] Aladhan API error: {e}")
        return None

    if data.get("code") != 200:
        logger.error(f"[prayer] Aladhan returned code {data.get('code')}")
        return None

    timings_raw = data["data"]["timings"]
    timings = {}
    for name in PRAYER_NAMES:
        time_str = timings_raw[name].split(" ")[0]
        timings[name] = time_str

    result = {
        "timings": timings,
        "date": data["data"]["date"]["readable"],
        "hijri_date": data["data"]["date"]["hijri"]["date"],
        "hijri_month": data["data"]["date"]["hijri"]["month"]["en"],
        "location": f"{geo['city']}, {geo['country']}",
    }

    _cache.clear()
    _cache[today] = result

    logger.info(f"[prayer] Fetched times for {geo['city']}: {timings}")
    return result


def get_next_prayer(timings: dict[str, str]) -> tuple[str | None, str | None, int | None]:
    """Return (name, time_str, minutes_until) for the next upcoming prayer, or (None, None, None)."""
    now = _now_wib()
    for name in PRAYER_NAMES:
        time_str = timings[name]
        hour, minute = map(int, time_str.split(":"))
        prayer_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        diff = (prayer_time - now).total_seconds() / 60
        if diff > -1:
            return name, time_str, int(diff)
    return None, None, None


async def get_due_reminders() -> list[dict]:
    """Check which prayer reminders are due now. Returns list of {name, time, minutes_until}."""
    global _sent_reminders

    today = _today_wib()
    day_prefix = today.replace("-", "")
    if _sent_reminders and not any(r.startswith(day_prefix) for r in _sent_reminders):
        _sent_reminders.clear()

    times = await fetch_prayer_times()
    if not times:
        return []

    now = _now_wib()
    due = []

    for name in PRAYER_NAMES:
        time_str = times["timings"][name]
        hour, minute = map(int, time_str.split(":"))
        prayer_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        minutes_until = int((prayer_time - now).total_seconds() / 60)

        for offset in REMINDER_OFFSETS:
            key = f"{day_prefix}-{name}-{offset}"
            if key in _sent_reminders:
                continue
            if 0 <= minutes_until <= offset:
                _sent_reminders.add(key)
                due.append({
                    "name": name,
                    "time": time_str,
                    "minutes_until": minutes_until,
                })
                break

    return due


async def check_health() -> str:
    """Health check — can we reach Aladhan API?"""
    try:
        times = await fetch_prayer_times()
        return "ok" if times else "error"
    except Exception:
        return "offline"
