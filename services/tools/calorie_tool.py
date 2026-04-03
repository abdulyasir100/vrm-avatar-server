"""Calorie tracking tools — LLM can add meals and check daily calories."""

import json
import logging
import asyncio
from typing import Any
import httpx
from openai import OpenAI
from services import tool_registry
import config

logger = logging.getLogger(__name__)

CALORIE_API = config.CALORIE_TRACKER_URL

_LLM_ESTIMATE_PROMPT = (
    'Estimate the nutrition for 1 typical serving of "{food}". '
    "Reply with ONLY a JSON object, no explanation: "
    '{{"calories": <int>, "protein_g": <float>, "carbs_g": <float>, "fat_g": <float>}}'
)


async def _api(method: str, path: str, json: dict | None = None, params: dict | None = None) -> dict | list | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(method, f"{CALORIE_API}{path}", json=json, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"[calorie_tool] API error: {method} {path} — {e}")
        return None


async def _estimate_with_llm(food_name: str) -> dict | None:
    """Ask LLM to estimate nutrition when all APIs fail. Better than 0."""
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

        raw = response.choices[0].message.content or ""
        # Extract JSON from response (LLM might wrap it in markdown)
        json_match = raw.strip()
        if "```" in json_match:
            import re
            m = re.search(r'\{[^}]+\}', json_match)
            if m:
                json_match = m.group(0)

        data = json.loads(json_match)
        calories = int(data.get("calories", 0))
        if calories <= 0:
            return None

        result = {
            "calories": calories,
            "protein_g": round(float(data.get("protein_g", 0)), 1),
            "carbs_g": round(float(data.get("carbs_g", 0)), 1),
            "fat_g": round(float(data.get("fat_g", 0)), 1),
            "source": "llm_estimate",
        }
        logger.info(f"[calorie_tool] LLM estimated {food_name}: {calories} cal")
        return result

    except Exception as e:
        logger.warning(f"[calorie_tool] LLM estimation failed for '{food_name}': {e}")
        return None


async def _add_single_meal(meal_type: str, food_name: str, portion: str) -> tuple[bool, str, int]:
    """Add a single meal item. Returns (ok, message, calories)."""
    nutrition = await _api("GET", "/lookup", params={"query": food_name})

    calories = 0
    protein_g = 0
    carbs_g = 0
    fat_g = 0
    source = "unknown"

    if nutrition and nutrition.get("found"):
        calories = nutrition.get("calories", 0)
        protein_g = nutrition.get("protein_g", 0)
        carbs_g = nutrition.get("carbs_g", 0)
        fat_g = nutrition.get("fat_g", 0)
        source = nutrition.get("source", "api")

    # LLM estimation fallback when lookup fails or returns 0 calories
    if calories <= 0:
        estimate = await _estimate_with_llm(food_name)
        if estimate:
            calories = estimate["calories"]
            protein_g = estimate["protein_g"]
            carbs_g = estimate["carbs_g"]
            fat_g = estimate["fat_g"]
            source = "llm_estimate"

    body = {
        "meal_type": meal_type,
        "food_name": food_name,
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "portion": portion,
    }

    result = await _api("POST", "/meals", json=body)
    if result is None:
        return False, food_name, 0

    return True, food_name, calories


async def handle_add_meal(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Add one or more meals with nutrition lookup.

    Arg format: meal_type|food_name or meal_type|food_name|portion
    Multiple items separated by comma: meal_type|food1,meal_type|food2
    Examples:
      "lunch|nasi goreng"
      "breakfast|kopi susu|1 cup"
      "dinner|squid black pepper,dinner|ice tea,dinner|red velvet boba"
    """
    if not arg.strip():
        return {"ok": False, "result": "No meal data provided", "side_effects": []}

    # Support multiple items separated by comma
    items = [i.strip() for i in arg.split(",")]
    total_calories = 0
    logged = []
    failed = []

    for item in items:
        parts = [p.strip() for p in item.split("|")]
        if len(parts) < 2:
            continue

        meal_type = parts[0].lower()
        if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
            meal_type = "snack"

        food_name = parts[1]
        portion = parts[2] if len(parts) >= 3 else "1 serving"

        ok, name, cals = await _add_single_meal(meal_type, food_name, portion)
        if ok:
            logged.append(name)
            total_calories += cals
        else:
            failed.append(name)

    if not logged:
        return {"ok": False, "result": "Calorie Tracker is offline", "side_effects": []}

    names = ", ".join(logged)
    if total_calories > 0:
        msg = f"Got it, logged {names}. Total about {total_calories} calories."
    else:
        msg = f"Got it, logged {names}, but I couldn't find the calorie info."

    return {"ok": True, "result": msg, "side_effects": []}


async def handle_check_calories(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Check today's calorie intake and status."""
    today = await _api("GET", "/today")

    if today is None:
        return {"ok": False, "result": "Calorie Tracker is offline", "side_effects": []}

    stats = today.get("stats", {})
    total = stats.get("total_calories", 0)
    target = stats.get("target_calories", 2000)
    remaining = stats.get("remaining", target)
    meal_count = stats.get("meal_count", 0)

    if meal_count == 0:
        return {"ok": True, "result": "You haven't eaten anything today! Go eat something.", "side_effects": []}

    if remaining > 0:
        summary = f"You've eaten {total} calories today out of {target}. Still have {remaining} calories left."
    else:
        summary = f"You've eaten {total} calories today, that's {abs(remaining)} over your {target} target!"

    return {"ok": True, "result": summary, "side_effects": []}


# Register on import
tool_registry.register("add_meal", handle_add_meal, schema={
    "name": "add_meal",
    "description": "Log a meal/food item with automatic nutrition lookup. Use when the user mentions eating something, having a meal, or logging food. IMPORTANT: Split each food/drink into a separate item — do NOT combine multiple foods into one item.",
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": "Pipe-separated: meal_type|food_name or meal_type|food_name|portion. For multiple items, comma-separate them. IMPORTANT: Each food and drink must be a separate item. Examples: 'lunch|nasi goreng', 'dinner|squid black pepper,dinner|ice tea,dinner|red velvet boba', 'breakfast|kopi susu|1 cup,breakfast|roti bakar'",
            }
        },
        "required": ["arg"],
    },
})
tool_registry.register("check_calories", handle_check_calories, schema={
    "name": "check_calories",
    "description": "Check today's calorie intake, remaining calories, and meal count. Use when the user asks about their calories, how much they've eaten, or their daily intake.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
})
