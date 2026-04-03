"""Habit tracker plugin — daily habits with streak tracking."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Habit storage not initialized")
    return _storage


async def handle_add_habit(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Add a new daily habit."""
    storage = _get_storage()
    name = arg.strip()
    if not name:
        return {"ok": False, "result": "Need a habit name.", "side_effects": []}

    existing = storage.find_by_name(name)
    if existing:
        return {"ok": False, "result": f"Already tracking '{existing['name']}'.", "side_effects": []}

    habit = storage.add_habit(name)
    return {"ok": True, "result": f"Now tracking: {habit['name']} (ID: {habit['id']})", "side_effects": []}


async def handle_check_habits(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """List all habits with today's status and streaks."""
    storage = _get_storage()
    habits = storage.get_all_with_status()

    if not habits:
        return {"ok": True, "result": "No habits tracked yet.", "side_effects": []}

    lines = []
    for h in habits:
        check = "done" if h["done_today"] else "pending"
        streak = h["streak"]
        fire = f" ({streak}d streak)" if streak > 0 else ""
        lines.append(f"- {h['name']} [{check}]{fire}")

    done = sum(1 for h in habits if h["done_today"])
    total = len(habits)
    lines.insert(0, f"Habits: {done}/{total} done today\n")

    return {"ok": True, "result": "\n".join(lines), "side_effects": []}


async def handle_complete_habit(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Mark a habit as done for today."""
    storage = _get_storage()
    name = arg.strip()
    if not name:
        return {"ok": False, "result": "Which habit?", "side_effects": []}

    # Try by ID first, then by name
    habit = storage.get_by_id(name) or storage.find_by_name(name)
    if not habit:
        return {"ok": False, "result": f"No habit matching '{name}'.", "side_effects": []}

    if storage.is_completed_today(habit["id"]):
        return {"ok": False, "result": f"'{habit['name']}' already done today.", "side_effects": []}

    storage.complete_today(habit["id"])
    streak = storage.get_streak(habit["id"])
    streak_msg = f" Streak: {streak} day{'s' if streak != 1 else ''}!" if streak > 0 else ""
    return {"ok": True, "result": f"'{habit['name']}' done!{streak_msg}", "side_effects": []}


async def check_incomplete():
    """Background check: return incomplete habits for nagging."""
    storage = _get_storage()
    incomplete = storage.get_incomplete_today()
    if not incomplete:
        return None

    names = ", ".join(h["name"] for h in incomplete[:3])
    count = len(incomplete)
    return [{
        "type": "habit_reminder",
        "count": count,
        "names": names,
        "prompt": f"The user has {count} incomplete habit(s) today: {names}. Nag them about it — be teasing but motivating. One sentence.",
    }]


async def handle_callback(action: str, item_id: str) -> dict | None:
    """Handle Telegram inline button callbacks."""
    storage = _get_storage()
    if action == "done":
        habit = storage.get_by_id(item_id)
        if not habit:
            return {"message": "Habit not found.", "refresh": False}
        if storage.is_completed_today(item_id):
            return {"message": f"'{habit['name']}' already done.", "refresh": False}
        storage.complete_today(item_id)
        streak = storage.get_streak(item_id)
        return {"message": f"'{habit['name']}' done! Streak: {streak}d", "refresh": True}
    return None


async def cmd_list(args: str = "") -> dict:
    """Show all habits with inline buttons to mark done."""
    storage = _get_storage()
    habits = storage.get_all_with_status()

    if not habits:
        return {"text": "No habits tracked. Use /p.habits.add <name>", "inline_keyboard": None}

    lines = []
    keyboard = []
    for h in habits:
        check = "done" if h["done_today"] else "pending"
        streak = h["streak"]
        fire = f" ({streak}d)" if streak > 0 else ""
        lines.append(f"{'[done]' if h['done_today'] else '[  ]'} {h['name']}{fire}")

        if not h["done_today"]:
            keyboard.append([
                {"text": f"Done: {h['name']}", "callback_data": f"plugin:habit:done:{h['id']}"}
            ])

    done = sum(1 for h in habits if h["done_today"])
    total = len(habits)
    header = f"Habits: {done}/{total} done today\n\n"

    return {"text": header + "\n".join(lines), "inline_keyboard": keyboard if keyboard else None}


async def cmd_add(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.habits.add <habit name>\nExample: /p.habits.add exercise", "inline_keyboard": None}
    result = await handle_add_habit(args, {})
    return {"text": result["result"], "inline_keyboard": None}


async def cmd_done(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.habits.done <habit name>", "inline_keyboard": None}
    result = await handle_complete_habit(args, {})
    return {"text": result["result"], "inline_keyboard": None}


async def cmd_remove(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.habits.remove <habit name>", "inline_keyboard": None}
    storage = _get_storage()
    habit = storage.get_by_id(args.strip()) or storage.find_by_name(args.strip())
    if not habit:
        return {"text": f"No habit matching '{args}'.", "inline_keyboard": None}
    storage.archive(habit["id"])
    return {"text": f"Removed: {habit['name']}", "inline_keyboard": None}
