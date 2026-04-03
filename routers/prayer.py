"""GET /prayer-times — Today's prayer times and next prayer."""

import logging
from fastapi import APIRouter
from services import prayer

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/prayer-times")
async def get_prayer_times():
    """Return today's prayer times (cached daily from Aladhan API)."""
    times = await prayer.fetch_prayer_times()
    if not times:
        return {"error": "Could not fetch prayer times"}

    next_name, next_time, minutes = prayer.get_next_prayer(times["timings"])

    return {
        "timings": times["timings"],
        "date": times["date"],
        "hijri_date": times["hijri_date"],
        "hijri_month": times["hijri_month"],
        "next_prayer": next_name,
        "next_prayer_time": next_time,
        "minutes_until_next": minutes,
    }
