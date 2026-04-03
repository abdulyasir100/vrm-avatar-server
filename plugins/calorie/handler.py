"""Calorie plugin — tool handlers for meal logging and calorie tracking."""

import json
import logging
import asyncio
from typing import Any
from openai import OpenAI
import config

logger = logging.getLogger(__name__)
_storage = None

_VALID_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}

_LLM_ESTIMATE_PROMPT = (
    'Estimate the nutrition for 1 typical serving of "{food}". '
    "Reply with ONLY a JSON object, no explanation: "
    '{{"calories": <int>, "protein_g": <float>, "carbs_g": <float>, "fat_g": <float>}}'
)


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Calorie plugin storage not initialized")
    return _storage


async def _estimate_with_llm(food_name: str) -> dict | None:
    """Ask LLM to estimate nutrition when local DB fails."""
    try:
        client = OpenAI(
            base_url=config.get_llm_base_url(),
            api_key=config.get_llm_api_key() or "no-key",
            timeout=10,
        )
        prompt = _LLM_ESTIMATE_PROMPT.format(food=food_name)
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model=config.get_llm_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=80,
            ),
            timeout=15,
        )
        text = response.choices[0].message.content or ""
        text = text.strip().strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        if data.get("calories", 0) > 0:
            logger.info(f"[calorie] LLM estimated {food_name}: {data['calories']} cal")
            return data
    except Exception as e:
        logger.warning(f"[calorie] LLM estimation failed for '{food_name}': {e}")
    return None


def _normalize_meal_type(raw: str) -> str:
    raw = raw.lower().strip()
    if raw in _VALID_MEAL_TYPES:
        return raw
    # Common aliases
    aliases = {
        "sarapan": "breakfast", "pagi": "breakfast",
        "siang": "lunch", "makan siang": "lunch",
        "malam": "dinner", "makan malam": "dinner",
        "cemilan": "snack", "jajan": "snack",
    }
    return aliases.get(raw, "snack")


async def handle_add_meal(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Log meals. Arg: 'meal_type|food_name' or multiple comma-separated."""
    storage = _get_storage()
    items = [i.strip() for i in arg.split(",") if i.strip()]
    if not items:
        return {"ok": False, "result": "No meal data provided.", "side_effects": []}

    total_cal = 0
    logged = []

    for item in items:
        parts = item.split("|")
        if len(parts) < 2:
            continue

        meal_type = _normalize_meal_type(parts[0])
        food_name = parts[1].strip()
        portion = parts[2].strip() if len(parts) > 2 else ""

        # Lookup nutrition: local DB → LLM estimation
        nutrition = storage.lookup_food(food_name)
        if not nutrition or nutrition.get("calories", 0) == 0:
            nutrition = await _estimate_with_llm(food_name)

        calories = nutrition.get("calories", 0) if nutrition else 0
        protein = nutrition.get("protein_g", 0.0) if nutrition else 0.0
        carbs = nutrition.get("carbs_g", 0.0) if nutrition else 0.0
        fat = nutrition.get("fat_g", 0.0) if nutrition else 0.0

        storage.create_meal(
            meal_type=meal_type,
            food_name=food_name,
            calories=calories,
            protein_g=protein,
            carbs_g=carbs,
            fat_g=fat,
            portion=portion,
        )

        total_cal += calories
        cal_str = f" (~{calories} cal)" if calories > 0 else ""
        logged.append(f"{food_name}{cal_str}")

    if not logged:
        return {"ok": False, "result": "Could not parse meal data.", "side_effects": []}

    summary = ", ".join(logged)
    return {
        "ok": True,
        "result": f"Logged: {summary}. Total about {total_cal} calories.",
        "side_effects": [],
    }


async def handle_check_calories(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check today's calorie intake."""
    storage = _get_storage()
    stats = storage.get_today_stats()

    total = stats["total_calories"]
    target = stats["daily_target"]
    remaining = stats["remaining"]
    count = stats["meal_count"]

    if count == 0:
        return {"ok": True, "result": "You haven't eaten anything today! Go eat something.", "side_effects": []}

    if remaining > 0:
        result = f"You've eaten {total}/{target} calories today ({count} meals). Still have {remaining} left."
    else:
        over = abs(remaining)
        result = f"You've eaten {total} calories today — that's {over} OVER your {target} target! ({count} meals)"

    return {"ok": True, "result": result, "side_effects": []}


async def handle_callback(action: str, item_id: str) -> dict | None:
    storage = _get_storage()
    if action == "delete":
        if storage.delete_meal(item_id):
            return {"message": "Meal deleted.", "refresh": True}
    return None


# --- Telegram command handlers (for /p.calories, /p.meals, /p.calories.target) ---

async def cmd_status(args: str = "") -> dict:
    """Show today's calorie summary."""
    storage = _get_storage()
    stats = storage.get_today_stats()
    total = stats["total_calories"]
    target = stats["daily_target"]
    remaining = stats["remaining"]
    count = stats["meal_count"]

    if count == 0:
        return {"text": "🍽 No meals logged today.", "inline_keyboard": None}

    pct = round((total / target) * 100) if target > 0 else 0
    bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))

    lines = [
        f"🍽 Calories Today",
        f"[{bar}] {pct}%",
        f"Eaten: {total} / {target} cal",
    ]
    if remaining > 0:
        lines.append(f"Remaining: {remaining} cal")
    else:
        lines.append(f"Over by: {abs(remaining)} cal ⚠️")
    lines.append(f"Meals: {count}")
    lines.append(f"P: {stats['total_protein']}g | C: {stats['total_carbs']}g | F: {stats['total_fat']}g")

    return {"text": "\n".join(lines), "inline_keyboard": None}


async def cmd_meals(args: str = "") -> dict:
    """Show today's meals with delete buttons."""
    storage = _get_storage()
    meals = storage.get_today_meals()
    if not meals:
        return {"text": "🍽 No meals today.", "inline_keyboard": None}

    lines = []
    rows = []
    for i, m in enumerate(meals, 1):
        cal = f" ({m['calories']} cal)" if m["calories"] > 0 else ""
        lines.append(f"{i}. {m['food_name']}{cal} [{m['meal_type']}]")
        rows.append([
            {"text": f"🗑 {m['food_name'][:15]}", "callback_data": f"plugin:calorie:delete:{m['id']}"},
        ])

    text = f"🍽 Today's Meals ({len(meals)}):\n" + "\n".join(lines)
    return {"text": text, "inline_keyboard": rows}


async def cmd_set_target(args: str = "") -> dict:
    """Set daily calorie target."""
    storage = _get_storage()
    if args:
        try:
            target = int(args.strip())
            storage.set_daily_target(target)
            return {"text": f"✅ Daily target set to {target} calories", "inline_keyboard": None}
        except ValueError:
            return {"text": "❌ Invalid number. Use: /p.calories.target 2000", "inline_keyboard": None}

    current = storage.get_daily_target()
    return {"text": f"Current target: {current} cal\nReply with new target: /p.calories.target 2000", "inline_keyboard": None}
