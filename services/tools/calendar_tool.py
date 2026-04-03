"""Calendar tool — LLM can check upcoming calendar events."""

import logging
from typing import Any
from services import tool_registry, calendar_service
from datetime import timezone, timedelta

logger = logging.getLogger(__name__)

# WIB = UTC+7
_WIB = timezone(timedelta(hours=7))


async def handle_check_calendar(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check upcoming calendar events (next 48 hours)."""
    events = await calendar_service.fetch_upcoming_events(hours_ahead=48)

    if events is None:
        return {"ok": False, "result": "Calendar service unavailable", "side_effects": []}

    if not events:
        return {"ok": True, "result": "No upcoming events in the next 48 hours.", "side_effects": []}

    lines = []
    for e in events[:10]:  # cap at 10
        start_wib = e["start"].astimezone(_WIB)
        time_str = start_wib.strftime("%a %d %b, %H:%M WIB")
        line = f"- {e['title']} — {time_str}"
        if e.get("location"):
            line += f" ({e['location']})"
        lines.append(line)

    summary = f"Upcoming events ({len(events)} total):\n" + "\n".join(lines)
    return {"ok": True, "result": summary, "side_effects": []}


# Register on import
tool_registry.register("check_calendar", handle_check_calendar, schema={
    "name": "check_calendar",
    "description": "Check upcoming calendar events for the next 48 hours. Use when the user asks about their schedule, meetings, appointments, or agenda.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
