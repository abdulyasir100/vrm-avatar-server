"""Sensor tools — check screen time and step count from Companion Sensor app."""

import logging
from typing import Any
from services import tool_registry, sensor

logger = logging.getLogger(__name__)


async def handle_check_screen_time(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check today's screen time per app."""
    data = sensor.get_today_summary()
    if not data or "screen_time" not in data:
        return {"ok": False, "result": "No screen time data available. Companion Sensor might be offline or hasn't reported yet.", "side_effects": []}

    st = data["screen_time"]
    if not st:
        return {"ok": True, "result": "No social media usage recorded today.", "side_effects": []}

    lines = [f"{app}: {mins} min" for app, mins in sorted(st.items(), key=lambda x: -x[1]) if mins > 0]
    total = sum(st.values())
    summary = f"Today's screen time ({total} min total): " + ", ".join(lines)
    return {"ok": True, "result": summary, "side_effects": []}


async def handle_check_steps(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check today's step count."""
    data = sensor.get_today_summary()
    if not data or "steps" not in data:
        return {"ok": False, "result": "No step data available. Companion Sensor might be offline or hasn't reported yet.", "side_effects": []}

    steps = data["steps"]
    return {"ok": True, "result": f"The user has walked {steps:,} steps today.", "side_effects": []}


tool_registry.register("check_screen_time", handle_check_screen_time, schema={
    "name": "check_screen_time",
    "description": "Check today's social media screen time per app. No arguments needed.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
})

tool_registry.register("check_steps", handle_check_steps, schema={
    "name": "check_steps",
    "description": "Check today's step count from the user's smartwatch. No arguments needed.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
})
