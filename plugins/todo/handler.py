"""Todo plugin — tool handlers for task management."""

import logging
import re
from datetime import datetime, timezone, timedelta

from utils import time as ltime
from typing import Any

logger = logging.getLogger(__name__)

# Storage module — set by plugin_loader after init
_storage = None


def set_storage(storage_module):
    """Called by plugin_loader after initializing storage."""
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Todo plugin storage not initialized")
    return _storage




def _parse_task_time(raw_title: str) -> tuple[str, str | None]:
    """Extract time expressions from task title, return (clean_title, due_time_utc_iso)."""
    time_patterns = [
        (r"\bat\s+(\d{1,2}):(\d{2})\b", None),
        (r"\bat\s+(\d{1,2})\s*(am|pm)\b", None),
        (r"\b(\d{1,2}):(\d{2})\b", None),
    ]

    day_offset = 0
    title = raw_title

    if re.search(r"\btomorrow\b|\bbesok\b", title, re.IGNORECASE):
        day_offset = 1
        title = re.sub(r"\b(?:tomorrow|besok)\b", "", title, flags=re.IGNORECASE)
    elif re.search(r"\blusa\b", title, re.IGNORECASE):
        day_offset = 2
        title = re.sub(r"\blusa\b", "", title, flags=re.IGNORECASE)
    elif re.search(r"\btonight\b", title, re.IGNORECASE):
        day_offset = 0
        title = re.sub(r"\btonight\b", "", title, flags=re.IGNORECASE)

    hour, minute = None, None

    # "at HH:MM"
    m = re.search(r"\bat\s+(\d{1,2}):(\d{2})\b", title)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        title = title[:m.start()] + title[m.end():]

    # "at Xam/pm"
    if hour is None:
        m = re.search(r"\bat\s+(\d{1,2})\s*(am|pm)\b", title, re.IGNORECASE)
        if m:
            hour = int(m.group(1))
            if m.group(2).lower() == "pm" and hour != 12:
                hour += 12
            elif m.group(2).lower() == "am" and hour == 12:
                hour = 0
            minute = 0
            title = title[:m.start()] + title[m.end():]

    # bare "HH:MM"
    if hour is None:
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", title)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            title = title[:m.start()] + title[m.end():]

    if hour is not None:
        now_wib = ltime.now()
        target = now_wib.replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
        target += timedelta(days=day_offset)
        if target < now_wib and day_offset == 0:
            target += timedelta(days=1)
        due_utc = target.astimezone(timezone.utc).isoformat()
        return title.strip().strip("|").strip(), due_utc

    return title.strip().strip("|").strip(), None


async def handle_add_task(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Add a new task. Arg: 'title' or 'title|priority|category'."""
    storage = _get_storage()
    parts = arg.split("|")
    raw_title = parts[0].strip()
    priority = parts[1].strip() if len(parts) > 1 else "medium"
    category = parts[2].strip() if len(parts) > 2 else ""

    if not raw_title:
        return {"ok": False, "result": "No task title provided.", "side_effects": []}

    title, due_time = _parse_task_time(raw_title)
    task = storage.create(title=title, priority=priority, category=category, due_time=due_time)

    due_str = ""
    if due_time:
        due_dt = datetime.fromisoformat(due_time).astimezone(ltime.get_tz())
        due_str = f" (due {ltime.fmt_time(due_dt)})"

    return {
        "ok": True,
        "result": f"Task added: \"{task['title']}\"{due_str}",
        "side_effects": [],
    }


async def handle_list_tasks(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """List tasks. Arg: '' (active), 'completed', 'all', 'high'/'medium'/'low', or category."""
    storage = _get_storage()
    arg = arg.strip().lower()

    if arg == "completed":
        tasks = storage.get_all(completed=True)
    elif arg == "all":
        tasks = storage.get_all()
    elif arg in ("high", "medium", "low"):
        tasks = storage.get_all(completed=False, priority=arg)
    elif arg:
        tasks = storage.get_all(completed=False, category=arg)
    else:
        tasks = storage.get_all(completed=False)

    if not tasks:
        return {"ok": True, "result": "No tasks found.", "side_effects": []}

    lines = []
    for i, t in enumerate(tasks[:5], 1):
        due = ""
        if t.get("due_time"):
            try:
                dt = datetime.fromisoformat(t["due_time"]).astimezone(ltime.get_tz())
                due = f" (due {dt.strftime('%H:%M')})"
            except Exception:
                pass
        status = "done" if t["completed"] else t["priority"]
        lines.append(f"{i}. {t['title']}{due} [{status}]")

    summary = "\n".join(lines)
    if len(tasks) > 5:
        summary += f"\n...and {len(tasks) - 5} more"

    # Build inline keyboard data for Telegram
    keyboard_data = []
    for t in tasks[:5]:
        if not t["completed"]:
            keyboard_data.append({
                "text": t["title"],
                "actions": [
                    {"label": "Done", "callback": f"plugin:todo:done:{t['id']}"},
                    {"label": "Delete", "callback": f"plugin:todo:delete:{t['id']}"},
                ],
            })

    side_effects = []
    if keyboard_data:
        side_effects.append({
            "type": "inline_keyboard",
            "items": keyboard_data,
        })

    return {"ok": True, "result": summary, "side_effects": side_effects}


async def handle_complete_task(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Complete a task by partial title match. Arg: partial title."""
    storage = _get_storage()
    search = arg.strip().lower()
    if not search:
        return {"ok": False, "result": "No task specified.", "side_effects": []}

    tasks = storage.get_all(completed=False)
    match = None
    for t in tasks:
        if search in t["title"].lower():
            match = t
            break

    if not match:
        return {"ok": False, "result": f"No active task matching \"{arg}\".", "side_effects": []}

    storage.complete(match["id"])
    return {"ok": True, "result": f"Completed: \"{match['title']}\"", "side_effects": []}


async def handle_callback(action: str, item_id: str) -> dict | None:
    """Handle Telegram inline button callbacks."""
    storage = _get_storage()

    if action == "done":
        task = storage.complete(item_id)
        if task:
            return {"message": f"Completed: \"{task['title']}\"", "refresh": True}
    elif action == "delete":
        task = storage.get_by_id(item_id)
        if task and storage.delete(item_id):
            return {"message": f"Deleted: \"{task['title']}\"", "refresh": True}

    return None


async def check_reminders():
    """Background check: get upcoming task reminders (called by plugin_loader)."""
    storage = _get_storage()
    return storage.get_upcoming_reminders(within_minutes=15)


# --- Telegram command handlers (for /p.tasks, /p.tasks.add, /p.tasks.done) ---

def _build_task_keyboard(tasks: list[dict]) -> list[list[dict]]:
    """Build inline keyboard rows for task list."""
    rows = []
    for t in tasks[:8]:
        rows.append([
            {"text": f"✅ {t['title'][:25]}", "callback_data": f"plugin:todo:done:{t['id']}"},
            {"text": "🗑", "callback_data": f"plugin:todo:delete:{t['id']}"},
        ])
    return rows


async def cmd_list(args: str = "") -> dict:
    """Show pending tasks with inline buttons."""
    storage = _get_storage()
    tasks = storage.get_all(completed=False)
    if not tasks:
        return {"text": "📋 No pending tasks.", "inline_keyboard": None}

    lines = []
    for i, t in enumerate(tasks[:8], 1):
        due = ""
        if t.get("due_time"):
            try:
                dt = datetime.fromisoformat(t["due_time"]).astimezone(ltime.get_tz())
                due = f" ⏰{dt.strftime('%H:%M')}"
            except Exception:
                pass
        pri = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t["priority"], "")
        lines.append(f"{i}. {pri} {t['title']}{due}")

    total = len(tasks)
    header = f"📋 Tasks ({total} pending)"
    if total > 8:
        header += f" — showing first 8"
    text = header + "\n" + "\n".join(lines)

    return {"text": text, "inline_keyboard": _build_task_keyboard(tasks)}


async def cmd_add(args: str = "") -> dict:
    """Prompt to add a task (user replies with task title)."""
    if args:
        result = await handle_add_task(args, {})
        return {"text": result["result"], "inline_keyboard": None}
    return {"text": "Reply with the task title to add:", "inline_keyboard": None}


async def cmd_done(args: str = "") -> dict:
    """Show completed tasks."""
    storage = _get_storage()
    tasks = storage.get_all(completed=True)
    if not tasks:
        return {"text": "✅ No completed tasks.", "inline_keyboard": None}

    lines = [f"{i}. ~~{t['title']}~~" for i, t in enumerate(tasks[:10], 1)]
    return {"text": "✅ Completed:\n" + "\n".join(lines), "inline_keyboard": None}
