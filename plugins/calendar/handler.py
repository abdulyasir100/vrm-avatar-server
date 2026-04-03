"""Calendar plugin — check upcoming events via Nextcloud CalDAV."""

import logging
from typing import Any
from datetime import timezone, timedelta
from services import calendar_service

logger = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))


async def handle_check_calendar(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    events = await calendar_service.fetch_upcoming_events(hours_ahead=48)
    if events is None:
        return {"ok": False, "result": "Calendar service unavailable", "side_effects": []}
    if not events:
        return {"ok": True, "result": "No upcoming events in the next 48 hours.", "side_effects": []}
    lines = []
    for e in events[:10]:
        start_wib = e["start"].astimezone(WIB)
        time_str = start_wib.strftime("%a %d %b, %H:%M WIB")
        line = f"- {e['title']} — {time_str}"
        if e.get("location"):
            line += f" ({e['location']})"
        lines.append(line)
    summary = f"Upcoming events ({len(events)} total):\n" + "\n".join(lines)
    return {"ok": True, "result": summary, "side_effects": []}


async def cmd_calendar(args: str = "") -> dict:
    events = await calendar_service.fetch_upcoming_events(hours_ahead=48)
    if events is None:
        return {"text": "📅 Calendar unavailable.", "inline_keyboard": None}
    if not events:
        return {"text": "📅 No upcoming events.", "inline_keyboard": None}
    lines = ["📅 Upcoming Events"]
    for e in events[:10]:
        start_wib = e["start"].astimezone(WIB)
        time_str = start_wib.strftime("%a %d %b, %H:%M")
        lines.append(f"• {e['title']} — {time_str}")
    return {"text": "\n".join(lines), "inline_keyboard": None}
