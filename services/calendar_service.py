"""Nextcloud CalDAV calendar service — fetches upcoming events for reminders and idle talk."""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

import config

logger = logging.getLogger(__name__)

_events_cache: list[dict] | None = None
_events_cache_time: float = 0.0
_CACHE_TTL = 15 * 60  # 15 minutes


def _fetch_events_sync(hours_ahead: int = 48) -> list[dict] | None:
    """Synchronous CalDAV fetch — run via asyncio.to_thread."""
    try:
        import caldav
    except ImportError:
        logger.error("[calendar] caldav package not installed")
        return None

    try:
        client = caldav.DAVClient(
            url=config.NEXTCLOUD_CALDAV_URL,
            username=config.NEXTCLOUD_USER,
            password=config.NEXTCLOUD_PASSWORD,
        )
        principal = client.principal()

        calendar = None
        for cal in principal.calendars():
            if cal.name and cal.name.lower() == config.NEXTCLOUD_CALENDAR_NAME.lower():
                calendar = cal
                break

        if calendar is None:
            calendars = principal.calendars()
            if calendars:
                calendar = calendars[0]
                logger.info(f"[calendar] Using fallback calendar: {calendar.name}")
            else:
                logger.warning("[calendar] No calendars found")
                return None

        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=hours_ahead)

        results = calendar.date_search(start=now, end=end, expand=True)
        events = []
        for event in results:
            try:
                vevent = event.vobject_instance.vevent
                title = str(vevent.summary.value) if hasattr(vevent, "summary") else "Untitled"
                start = vevent.dtstart.value
                end_dt = vevent.dtend.value if hasattr(vevent, "dtend") else None
                location = str(vevent.location.value) if hasattr(vevent, "location") else None

                if isinstance(start, datetime):
                    if start.tzinfo is None:
                        start = start.replace(tzinfo=timezone.utc)
                else:
                    start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)

                if end_dt and isinstance(end_dt, datetime):
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                elif end_dt:
                    end_dt = datetime.combine(end_dt, datetime.min.time(), tzinfo=timezone.utc)

                events.append({
                    "title": title,
                    "start": start,
                    "end": end_dt,
                    "location": location,
                })
            except Exception as e:
                logger.warning(f"[calendar] Failed to parse event: {e}")

        events.sort(key=lambda e: e["start"])
        return events

    except Exception as e:
        logger.warning(f"[calendar] CalDAV error: {e}")
        return None


async def fetch_upcoming_events(hours_ahead: int = 48) -> list[dict] | None:
    """Fetch upcoming events. Cached for 15 minutes."""
    global _events_cache, _events_cache_time

    if _events_cache is not None and time.time() - _events_cache_time < _CACHE_TTL:
        return _events_cache

    events = await asyncio.to_thread(_fetch_events_sync, hours_ahead)
    if events is not None:
        _events_cache = events
        _events_cache_time = time.time()
        logger.info(f"[calendar] Cached {len(events)} upcoming events")
    return events


async def get_events_within(minutes: int = 30) -> list[dict]:
    """Get events starting within the next N minutes."""
    events = await fetch_upcoming_events()
    if not events:
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=minutes)

    return [e for e in events if now <= e["start"] <= cutoff]


async def check_health() -> str:
    """Health check — can we connect to Nextcloud CalDAV?"""
    if not config.NEXTCLOUD_ENABLED:
        return "disabled"
    try:
        events = await fetch_upcoming_events()
        return "ok" if events is not None else "offline"
    except Exception:
        return "offline"
