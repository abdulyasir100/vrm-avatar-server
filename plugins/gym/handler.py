"""Gym plugin — workout planner with setup wizard, daily plans, and tracking."""

import logging
from typing import Any

from plugins.gym.planner import SPLITS, generate_plan, format_workout, format_week

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Gym storage not initialized")
    return _storage


# --- Tool handlers ---

async def handle_check_workout(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Show today's workout or weekly schedule."""
    storage = _get_storage()

    if not storage.has_plan():
        return {"ok": False, "result": "No workout plan yet. Use /p.gym.setup to create one.", "side_effects": []}

    if arg.strip().lower() == "week":
        plan = storage.get_plan()
        completions = storage.get_week_completions()
        text = format_week(plan, completions)
        streak = storage.get_streak()
        if streak > 0:
            text += f"\n\nStreak: {streak} workout{'s' if streak != 1 else ''}"
        return {"ok": True, "result": text, "side_effects": []}

    today = storage.get_today_plan()
    if not today:
        return {"ok": True, "result": "No workout scheduled for today.", "side_effects": []}

    done = storage.is_completed_today()
    text = format_workout(today)
    if done:
        text += "\n\n(Already completed today!)"
    return {"ok": True, "result": text, "side_effects": []}


async def handle_complete_workout(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Mark today's workout as done."""
    storage = _get_storage()

    if not storage.has_plan():
        return {"ok": False, "result": "No workout plan. Use /p.gym.setup first.", "side_effects": []}

    today = storage.get_today_plan()
    if not today or today["type"].lower() == "rest":
        return {"ok": False, "result": "Today is a rest day — no workout to complete.", "side_effects": []}

    if storage.is_completed_today():
        return {"ok": False, "result": "Already marked as done today!", "side_effects": []}

    storage.complete_today(arg.strip())
    streak = storage.get_streak()
    streak_msg = f" Streak: {streak} day{'s' if streak != 1 else ''}!" if streak > 0 else ""
    return {"ok": True, "result": f"Workout done!{streak_msg}", "side_effects": []}


# --- Background ---

async def check_workout_reminder():
    """Background check: remind about today's workout."""
    storage = _get_storage()
    if not storage.has_plan():
        return None

    today = storage.get_today_plan()
    if not today or today["type"].lower() == "rest":
        return None

    if storage.is_completed_today():
        return None

    return [{
        "type": "workout_reminder",
        "workout": today["type"],
        "prompt": f"The user hasn't done their workout today ({today['type']}). Nag them about it — be motivating. One sentence.",
    }]


async def scheduled_morning():
    """Morning scheduled action: return today's workout as prompt."""
    storage = _get_storage()
    if not storage.has_plan():
        return None

    today = storage.get_today_plan()
    if not today:
        return None

    if today["type"].lower() == "rest":
        return "Today is a rest day from the gym. Tell the user to stretch and recover. One sentence."

    exercises = ", ".join(ex["name"] for ex in today["exercises"][:4])
    return (
        f"Today's workout is {today['type']}: {exercises}. "
        f"Announce this naturally — motivate them to do it. One sentence."
    )


# --- Callbacks ---

async def handle_callback(action: str, item_id: str) -> dict | None:
    """Handle setup wizard button callbacks."""
    storage = _get_storage()

    if action == "select_split":
        split_key = item_id
        if split_key not in SPLITS:
            return {"message": "Invalid selection.", "refresh": False}

        plan = generate_plan(split_key)
        storage.save_plan(plan)
        storage.set_profile("split", split_key)
        storage.set_profile("split_name", SPLITS[split_key]["name"])

        plan_data = storage.get_plan()
        text = f"Plan set: {SPLITS[split_key]['name']}\n\n{format_week(plan_data)}"
        text += "\n\nUse /p.gym to see today's workout!"
        return {"message": text, "refresh": False}

    if action == "done":
        today = storage.get_today_plan()
        if not today or today["type"].lower() == "rest":
            return {"message": "Rest day!", "refresh": False}
        if storage.is_completed_today():
            return {"message": "Already done!", "refresh": False}
        storage.complete_today()
        streak = storage.get_streak()
        return {"message": f"Workout done! Streak: {streak}d", "refresh": True}

    return None


# --- Telegram commands ---

async def cmd_today(args: str = "") -> dict:
    """Show today's workout with done button."""
    storage = _get_storage()

    if not storage.has_plan():
        return {"text": "No plan yet. Use /p.gym.setup", "inline_keyboard": None}

    today = storage.get_today_plan()
    if not today:
        return {"text": "No workout data for today.", "inline_keyboard": None}

    text = format_workout(today)
    done = storage.is_completed_today()

    keyboard = None
    if not done and today["type"].lower() != "rest":
        keyboard = [[{"text": "Done!", "callback_data": "plugin:gym:done:today"}]]

    if done:
        streak = storage.get_streak()
        text += f"\n\nDone! Streak: {streak}d"

    return {"text": text, "inline_keyboard": keyboard}


async def cmd_week(args: str = "") -> dict:
    """Show full weekly plan."""
    storage = _get_storage()

    if not storage.has_plan():
        return {"text": "No plan yet. Use /p.gym.setup", "inline_keyboard": None}

    plan = storage.get_plan()
    completions = storage.get_week_completions()
    text = format_week(plan, completions)

    streak = storage.get_streak()
    if streak > 0:
        text += f"\n\nStreak: {streak} workout{'s' if streak != 1 else ''}"

    profile = storage.get_all_profile()
    if profile.get("split_name"):
        text = f"Plan: {profile['split_name']}\n\n{text}"

    return {"text": text, "inline_keyboard": None}


async def cmd_done(args: str = "") -> dict:
    result = await handle_complete_workout(args, {})
    return {"text": result["result"], "inline_keyboard": None}


async def cmd_setup(args: str = "") -> dict:
    """Setup wizard — show split options as buttons."""
    keyboard = []
    for key, split in SPLITS.items():
        keyboard.append([{
            "text": f"{split['name']} — {split['description']}",
            "callback_data": f"plugin:gym:select_split:{key}",
        }])

    text = (
        "Workout Setup\n\n"
        "Equipment: Dumbbells + Mini Stepper\n"
        "Schedule: 4 days on / 3 rest\n\n"
        "Choose your split:"
    )
    return {"text": text, "inline_keyboard": keyboard}


async def cmd_reset(args: str = "") -> dict:
    storage = _get_storage()
    storage.reset()
    return {"text": "Workout plan deleted. Use /p.gym.setup to create a new one.", "inline_keyboard": None}
