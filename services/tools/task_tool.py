"""Task management tools — LLM can add, list, and complete tasks via the Task Manager API."""

import re
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
import httpx
from services import tool_registry
import config

logger = logging.getLogger(__name__)

TASK_API = config.TASK_MANAGER_URL


async def _api(method: str, path: str, json: dict | None = None) -> dict | list | None:
    """Make an async HTTP request to the Task Manager API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, f"{TASK_API}{path}", json=json)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"[task_tool] API error: {method} {path} — {e}")
        return None


_WIB = timezone(timedelta(hours=7))

# Time extraction patterns
_TIME_PATTERN = re.compile(
    r"(?:at|@)\s+(\d{1,2})(?::(\d{2}))?\s*([APap][Mm])?"
    r"|(\d{1,2}):(\d{2})\s*([APap][Mm])?",
    re.IGNORECASE,
)
_RELATIVE_DAY_PATTERN = re.compile(
    r"\b(today|tonight|tomorrow|lusa)\b",
    re.IGNORECASE,
)


def _parse_task_time(text: str) -> tuple[str, datetime | None]:
    """Extract time and clean title from task text.

    Returns: (clean_title, due_time_utc)
    """
    due_time = None
    clean = text

    # Extract time (e.g., "at 18:00", "at 6PM", "14:30")
    time_match = _TIME_PATTERN.search(clean)
    hour, minute = None, None
    if time_match:
        if time_match.group(1) is not None:
            # "at 18:00" or "at 6PM" format
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            ampm = (time_match.group(3) or "").upper()
        else:
            # "14:30" format
            hour = int(time_match.group(4))
            minute = int(time_match.group(5) or 0)
            ampm = (time_match.group(6) or "").upper()

        if ampm == "PM" and hour < 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0

        clean = clean[:time_match.start()] + clean[time_match.end():]

    # Extract relative day
    now_wib = datetime.now(_WIB)
    day_offset = 0
    day_match = _RELATIVE_DAY_PATTERN.search(clean)
    if day_match:
        word = day_match.group(1).lower()
        if word in ("today", "tonight"):
            day_offset = 0
        elif word == "tomorrow":
            day_offset = 1
        elif word == "lusa":  # Indonesian: day after tomorrow
            day_offset = 2
        clean = clean[:day_match.start()] + clean[day_match.end():]

    # Build due_time if we have a time
    if hour is not None:
        target_date = now_wib.date() + timedelta(days=day_offset)
        due_wib = datetime(target_date.year, target_date.month, target_date.day,
                           hour, minute, tzinfo=_WIB)
        # If the time already passed today and no explicit day, push to tomorrow
        if day_offset == 0 and not day_match and due_wib <= now_wib:
            due_wib += timedelta(days=1)
        due_time = due_wib.astimezone(timezone.utc)

    # Clean up leftover whitespace and trailing punctuation
    clean = re.sub(r"\s{2,}", " ", clean).strip().rstrip(".,;:!- ")

    return clean, due_time


async def handle_add_task(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Add a new task.

    Arg format: title (simple) or title|priority|category
    Time expressions in title are auto-parsed:
      "Buy groceries at 18:00" → due_time = today 18:00
      "Review code tomorrow at 14:00|high|work"
      "Water plants daily at 08:00"
    """
    parts = [p.strip() for p in arg.split("|")]
    raw_title = parts[0]
    if not raw_title:
        return {"ok": False, "result": "No task title provided", "side_effects": []}

    # Parse time from the title
    title, due_time = _parse_task_time(raw_title)
    if not title:
        title = raw_title  # fallback if parsing ate everything

    body: dict[str, Any] = {"title": title}
    if len(parts) >= 2 and parts[1] in ("low", "medium", "high"):
        body["priority"] = parts[1]
    if len(parts) >= 3 and parts[2]:
        body["category"] = parts[2]
    if due_time:
        body["due_time"] = due_time.isoformat()

    result = await _api("POST", "/tasks", json=body)
    if result is None:
        return {"ok": False, "result": "Task Manager is offline", "side_effects": []}

    return {
        "ok": True,
        "result": f"Added task: {title}." + (f" Due at {due_time.astimezone(_WIB).strftime('%H:%M')}." if due_time else ""),
        "side_effects": [],
    }


async def handle_list_tasks(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    List active tasks, optionally filtered.

    Arg: empty (all active) or a filter keyword: "all", "high", "work", "completed"
    """
    params = ""
    arg = arg.strip().lower()
    if arg == "completed":
        params = "?completed=true"
    elif arg == "all":
        params = ""
    elif arg in ("high", "medium", "low"):
        params = f"?completed=false&priority={arg}"
    elif arg:
        params = f"?completed=false&category={arg}"
    else:
        params = "?completed=false"

    result = await _api("GET", f"/tasks{params}")
    if result is None:
        return {"ok": False, "result": "Task Manager is offline", "side_effects": []}

    if not result:
        return {"ok": True, "result": "You have no tasks right now.", "side_effects": []}

    # Format as natural speech
    titles = [t["title"] for t in result[:5]]
    total = len(result)
    if total == 1:
        summary = f"You have one task: {titles[0]}."
    elif total <= 5:
        summary = f"You have {total} tasks: {', '.join(titles[:-1])}, and {titles[-1]}."
    else:
        summary = f"You have {total} tasks. Top ones: {', '.join(titles)}."

    return {"ok": True, "result": summary, "side_effects": []}


async def handle_complete_task(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Mark a task as complete by searching for it by title substring.

    Arg: partial title match (e.g., "groceries", "review code")
    """
    search = arg.strip().lower()
    if not search:
        return {"ok": False, "result": "No task title provided to complete", "side_effects": []}

    # Get active tasks and find the best match
    tasks = await _api("GET", "/tasks?completed=false")
    if tasks is None:
        return {"ok": False, "result": "Task Manager is offline", "side_effects": []}

    # Find first task whose title contains the search term
    match = None
    for t in tasks:
        if search in t["title"].lower():
            match = t
            break

    if not match:
        return {
            "ok": False,
            "result": f"No active task matching \"{arg}\"",
            "side_effects": [],
        }

    # Toggle complete
    result = await _api("PATCH", f"/tasks/{match['id']}/complete")
    if result is None:
        return {"ok": False, "result": "Failed to complete task", "side_effects": []}

    return {
        "ok": True,
        "result": f"Done! Marked {match['title']} as completed.",
        "side_effects": [],
    }


# Register on import
tool_registry.register("add_task", handle_add_task, schema={
    "name": "add_task",
    "description": "Add a new task/to-do item. Use when the user wants to remember something, add a reminder, or create a task. Time expressions like 'at 18:00' or 'tomorrow at 14:00' are auto-parsed.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Task title, optionally with time and pipe-separated priority/category. Examples: 'Buy groceries at 18:00', 'Review code tomorrow at 14:00|high|work', 'Water plants daily at 08:00'",
            }
        },
        "required": ["arg"],
    },
})
tool_registry.register("list_tasks", handle_list_tasks, schema={
    "name": "list_tasks",
    "description": "List active tasks/to-do items. Use when the user asks what's on their list, what they need to do, or to show tasks.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Optional filter: 'all', 'high', 'medium', 'low', 'completed', or a category name. Leave empty for all active tasks.",
            }
        },
        "required": [],
    },
})
tool_registry.register("complete_task", handle_complete_task, schema={
    "name": "complete_task",
    "description": "Mark a task as completed. Use when the user says they finished, completed, or are done with something.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Partial title to match against active tasks. Example: 'groceries', 'review code'",
            }
        },
        "required": ["arg"],
    },
})
