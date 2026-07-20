"""Calorie plugin — intent-first gatekeeping.

Flow change (was retrospective add_meal, now pre-decision check_meal):
- User states intent: "i'm gonna eat a burger"
- The character calls check_meal → tool returns nutrition + today's totals + verdict
- Auto-logs if verdict is yes/warn (status='approved')
- Doesn't log if verdict is no — caches as last_rejected_meal in settings
- If user says "eating anyway", the character calls override_meal which pulls the
  cached intent and logs it with status='overruled'

This is intentionally one-shot: every intent statement gets one tool call.
The LLM phrases the verdict in-character based on the structured result.
"""

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

_LLM_PLAN_CHECK_PROMPT = (
    'A user is on this diet plan: "{plan}".\n'
    'They are about to eat: {foods}.\n'
    'Macros for this intake: {macros_summary}.\n'
    'Rate alignment with their plan. Reply ONLY with a JSON object:\n'
    '{{"alignment": "aligned" | "warning" | "conflicts", "reason": "<one short sentence>"}}'
)

# Budget verdict thresholds (calorie totals vs daily target).
# - yes: projected ≤ 100% of target
# - warn: projected 100-110% (slight overshoot)
# - no: projected > 110% of target
_WARN_OVERSHOOT_PCT = 1.10

# How a plan-compliance result downgrades the budget verdict.
# Matrix: rows = budget verdict, cols = plan alignment → final verdict.
_VERDICT_MATRIX = {
    ("yes", "aligned"): "yes",
    ("yes", "warning"): "warn",
    ("yes", "conflicts"): "no",
    ("warn", "aligned"): "warn",
    ("warn", "warning"): "no",
    ("warn", "conflicts"): "no",
    ("no", "aligned"): "no",
    ("no", "warning"): "no",
    ("no", "conflicts"): "no",
}


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Calorie plugin storage not initialized")
    return _storage


def _estimation_endpoints() -> list[dict]:
    """Endpoints to try for nutrition estimation, in order.

    Claude CLI runs as a subprocess and isn't OpenAI-compatible, so when the
    primary provider is 'claude' we skip it and use the fallback chain
    (typically Groq → Cerebras) which both speak the OpenAI HTTP API.
    """
    chain: list[dict] = []
    if config.LLM_PROVIDER != "claude":
        chain.append({
            "base_url": config.get_llm_base_url(),
            "api_key": config.get_llm_api_key() or "no-key",
            "model": config.get_llm_model(),
        })
    for fb in config.get_llm_fallback_chain():
        if fb.get("api_key"):
            chain.append({
                "base_url": fb["base_url"],
                "api_key": fb["api_key"],
                "model": fb["model"],
            })
    return chain


async def _ask_llm_json(prompt: str, max_tokens: int = 100) -> dict | None:
    """Send a prompt to the first reachable OpenAI-compatible endpoint, parse JSON reply."""
    for endpoint in _estimation_endpoints():
        try:
            client = OpenAI(
                base_url=endpoint["base_url"],
                api_key=endpoint["api_key"],
                timeout=10,
            )
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat.completions.create,
                    model=endpoint["model"],
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=max_tokens,
                ),
                timeout=15,
            )
            text = (response.choices[0].message.content or "").strip().strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()
            return json.loads(text)
        except Exception as e:
            logger.warning(
                f"[calorie] LLM JSON call via {endpoint.get('base_url')} failed: {e}"
            )
            continue
    return None


async def _check_plan_alignment(plan: str, items: list[dict]) -> dict | None:
    """Ask the LLM whether the intended meals fit the user's diet plan.

    Returns {alignment: 'aligned'|'warning'|'conflicts', reason: str} or None
    if the plan is empty or the LLM call failed.
    """
    if not plan or not plan.strip():
        return None
    foods = ", ".join(f"{p['food_name']} (~{p['calories']} cal)" for p in items)
    total_p = round(sum(p["protein_g"] for p in items), 1)
    total_c = round(sum(p["carbs_g"] for p in items), 1)
    total_f = round(sum(p["fat_g"] for p in items), 1)
    macros_summary = f"~{sum(p['calories'] for p in items)} cal, P:{total_p}g C:{total_c}g F:{total_f}g"
    prompt = _LLM_PLAN_CHECK_PROMPT.format(
        plan=plan.strip(),
        foods=foods,
        macros_summary=macros_summary,
    )
    data = await _ask_llm_json(prompt, max_tokens=120)
    if not data:
        return None
    alignment = (data.get("alignment") or "").lower().strip()
    if alignment not in ("aligned", "warning", "conflicts"):
        logger.warning(f"[calorie] plan check returned unknown alignment: {alignment!r}")
        return None
    return {
        "alignment": alignment,
        "reason": (data.get("reason") or "").strip(),
    }


async def _estimate_with_llm(food_name: str) -> dict | None:
    """Ask an OpenAI-compatible LLM to estimate nutrition when local DB fails."""
    prompt = _LLM_ESTIMATE_PROMPT.format(food=food_name)
    for endpoint in _estimation_endpoints():
        try:
            client = OpenAI(
                base_url=endpoint["base_url"],
                api_key=endpoint["api_key"],
                timeout=10,
            )
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.chat.completions.create,
                    model=endpoint["model"],
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
                logger.info(
                    f"[calorie] estimated '{food_name}' via {endpoint['base_url']}: "
                    f"{data['calories']} cal"
                )
                return data
        except Exception as e:
            logger.warning(
                f"[calorie] estimation via {endpoint.get('base_url')} failed for "
                f"'{food_name}': {e}"
            )
            continue
    return None


def _normalize_meal_type(raw: str) -> str:
    raw = raw.lower().strip()
    if raw in _VALID_MEAL_TYPES:
        return raw
    aliases = {
        "sarapan": "breakfast", "pagi": "breakfast",
        "siang": "lunch", "makan siang": "lunch",
        "malam": "dinner", "makan malam": "dinner",
        "cemilan": "snack", "jajan": "snack",
    }
    return aliases.get(raw, "snack")


async def _resolve_nutrition(food_name: str) -> dict:
    """Local DB → LLM estimation. Returns {calories, protein_g, carbs_g, fat_g}."""
    storage = _get_storage()
    nutrition = storage.lookup_food(food_name)
    if not nutrition or nutrition.get("calories", 0) == 0:
        nutrition = await _estimate_with_llm(food_name)
    if not nutrition:
        return {"calories": 0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    return {
        "calories": int(nutrition.get("calories", 0)),
        "protein_g": float(nutrition.get("protein_g", 0.0)),
        "carbs_g": float(nutrition.get("carbs_g", 0.0)),
        "fat_g": float(nutrition.get("fat_g", 0.0)),
    }


def _verdict(projected_total: int, target: int) -> str:
    """yes / warn / no based on projected daily total vs target."""
    if target <= 0:
        return "warn"
    ratio = projected_total / target
    if ratio <= 1.0:
        return "yes"
    if ratio <= _WARN_OVERSHOOT_PCT:
        return "warn"
    return "no"


async def _parse_meal_items(arg: str) -> list[dict]:
    """Parse 'meal_type|food_name[|portion]' items (comma-separated) and resolve nutrition.

    Shared by check_meal (pre-decision) and log_meal (retrospective).
    """
    parsed = []
    for item in [i.strip() for i in arg.split(",") if i.strip()]:
        parts = item.split("|")
        if len(parts) < 2:
            continue
        meal_type = _normalize_meal_type(parts[0])
        food_name = parts[1].strip()
        portion = parts[2].strip() if len(parts) > 2 else ""
        nutrition = await _resolve_nutrition(food_name)
        parsed.append({
            "meal_type": meal_type,
            "food_name": food_name,
            "portion": portion,
            **nutrition,
        })
    return parsed


async def handle_check_meal(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Analyse intended meal vs today's totals and decide.

    Arg: 'meal_type|food_name' or 'meal_type|food_name|portion'.
    Multiple comma-separated for combined intent ('snack|kfc original,snack|coke').

    On verdict yes/warn → logs the meal as approved and returns the verdict.
    On verdict no → caches food info as last_rejected_meal and returns the verdict
    without logging. The character renders the structured response in their voice.
    """
    storage = _get_storage()
    if not arg.strip():
        return {"ok": False, "result": "No meal data provided.", "side_effects": []}

    today_stats = storage.get_today_stats()
    today_total = today_stats["total_calories"]
    target = today_stats["daily_target"]

    parsed = await _parse_meal_items(arg)
    if not parsed:
        return {"ok": False, "result": "Could not parse meal data.", "side_effects": []}

    intent_calories = sum(p["calories"] for p in parsed)
    intent_protein = round(sum(p["protein_g"] for p in parsed), 1)
    intent_carbs = round(sum(p["carbs_g"] for p in parsed), 1)
    intent_fat = round(sum(p["fat_g"] for p in parsed), 1)
    projected_total = today_total + intent_calories
    budget_verdict = _verdict(projected_total, target)
    remaining_after = target - projected_total

    # Plan compliance — only runs if user has set a diet_plan.
    diet_plan = storage.get_setting("diet_plan") or ""
    plan_check = await _check_plan_alignment(diet_plan, parsed) if diet_plan else None
    if plan_check:
        verdict = _VERDICT_MATRIX.get((budget_verdict, plan_check["alignment"]), budget_verdict)
    else:
        verdict = budget_verdict

    food_summary = ", ".join(f"{p['food_name']} (~{p['calories']} cal)" for p in parsed)
    macros = f"P:{intent_protein}g C:{intent_carbs}g F:{intent_fat}g"
    plan_note = ""
    if plan_check:
        if plan_check["alignment"] == "aligned":
            plan_note = f" Plan-aligned: {plan_check['reason']}" if plan_check["reason"] else ""
        elif plan_check["alignment"] == "warning":
            plan_note = f" Plan caveat: {plan_check['reason']}" if plan_check["reason"] else ""
        else:  # conflicts
            plan_note = f" Conflicts with plan: {plan_check['reason']}" if plan_check["reason"] else ""

    if verdict == "yes":
        for p in parsed:
            storage.create_meal(
                meal_type=p["meal_type"],
                food_name=p["food_name"],
                calories=p["calories"],
                protein_g=p["protein_g"],
                carbs_g=p["carbs_g"],
                fat_g=p["fat_g"],
                portion=p["portion"],
                status="approved",
            )
        storage.clear_setting("last_rejected_meal")
        result = (
            f"Approved and logged: {food_summary} = ~{intent_calories} cal ({macros}). "
            f"Today's at {projected_total}/{target} — {remaining_after} left.{plan_note}"
        )
    elif verdict == "warn":
        for p in parsed:
            storage.create_meal(
                meal_type=p["meal_type"],
                food_name=p["food_name"],
                calories=p["calories"],
                protein_g=p["protein_g"],
                carbs_g=p["carbs_g"],
                fat_g=p["fat_g"],
                portion=p["portion"],
                status="approved",
            )
        storage.clear_setting("last_rejected_meal")
        over = projected_total - target
        over_str = f"{over} over the line" if over > 0 else f"under budget"
        result = (
            f"Tight call but logged: {food_summary} = ~{intent_calories} cal ({macros}). "
            f"Today's now {projected_total}/{target} — {over_str}. Last call before damage.{plan_note}"
        )
    else:
        storage.set_setting(
            "last_rejected_meal",
            json.dumps({
                "items": parsed,
                "intent_calories": intent_calories,
            }),
        )
        over = projected_total - target
        if over > 0:
            reason = (
                f"would push you {over} cal over today's {target} target "
                f"(current: {today_total})"
            )
        else:
            reason = "doesn't fit your plan"
        result = (
            f"Skip it. {food_summary} = ~{intent_calories} cal {reason}.{plan_note} "
            f"Halve it, pick something lighter, or say 'eating anyway' to overrule me."
        )

    return {"ok": True, "result": result, "side_effects": []}


async def handle_log_meal(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Log a meal the user has ALREADY eaten / is currently eating.

    Arg: same format as check_meal ('meal_type|food_name[|portion]', comma-separated).

    Retrospective, not a gatekeeper: it ALWAYS logs (status='approved') because the
    food is already in the user's stomach — there's nothing to approve or reject.
    It still reports today's totals and flags an overshoot so the character can roast.
    """
    storage = _get_storage()
    if not arg.strip():
        return {"ok": False, "result": "No meal data provided.", "side_effects": []}

    today_stats = storage.get_today_stats()
    today_total = today_stats["total_calories"]
    target = today_stats["daily_target"]

    parsed = await _parse_meal_items(arg)
    if not parsed:
        return {"ok": False, "result": "Could not parse meal data.", "side_effects": []}

    eaten_calories = sum(p["calories"] for p in parsed)
    eaten_protein = round(sum(p["protein_g"] for p in parsed), 1)
    eaten_carbs = round(sum(p["carbs_g"] for p in parsed), 1)
    eaten_fat = round(sum(p["fat_g"] for p in parsed), 1)
    projected_total = today_total + eaten_calories
    remaining_after = target - projected_total

    for p in parsed:
        storage.create_meal(
            meal_type=p["meal_type"],
            food_name=p["food_name"],
            calories=p["calories"],
            protein_g=p["protein_g"],
            carbs_g=p["carbs_g"],
            fat_g=p["fat_g"],
            portion=p["portion"],
            status="approved",
        )

    food_summary = ", ".join(f"{p['food_name']} (~{p['calories']} cal)" for p in parsed)
    macros = f"P:{eaten_protein}g C:{eaten_carbs}g F:{eaten_fat}g"

    if target > 0 and projected_total > target:
        over = projected_total - target
        result = (
            f"Logged (already eaten): {food_summary} = ~{eaten_calories} cal ({macros}). "
            f"Too late to stop you — today's at {projected_total}/{target}, {over} OVER the target. "
            f"Nothing to approve, the damage is done."
        )
    else:
        result = (
            f"Logged (already eaten): {food_summary} = ~{eaten_calories} cal ({macros}). "
            f"Today's at {projected_total}/{target} — {remaining_after} left. Counted, you're still fine."
        )

    return {"ok": True, "result": result, "side_effects": []}


async def handle_override_meal(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Log the last rejected meal with status='overruled'.

    Triggered when user explicitly defies a 'no' verdict ('eating anyway',
    'eating it regardless', 'fuck it eating it'). Arg is ignored — the
    most recent check_meal rejection is what gets logged.
    """
    storage = _get_storage()
    raw = storage.get_setting("last_rejected_meal")
    if not raw:
        return {
            "ok": False,
            "result": "No pending rejected meal to override. Tell the character what you're eating first.",
            "side_effects": [],
        }
    try:
        cached = json.loads(raw)
        items = cached.get("items", [])
    except (json.JSONDecodeError, TypeError):
        items = []
    if not items:
        storage.clear_setting("last_rejected_meal")
        return {
            "ok": False,
            "result": "Cached rejection was malformed.",
            "side_effects": [],
        }

    for p in items:
        storage.create_meal(
            meal_type=p["meal_type"],
            food_name=p["food_name"],
            calories=p["calories"],
            protein_g=p["protein_g"],
            carbs_g=p["carbs_g"],
            fat_g=p["fat_g"],
            portion=p["portion"],
            status="overruled",
        )
    storage.clear_setting("last_rejected_meal")

    food_summary = ", ".join(f"{p['food_name']} (~{p['calories']} cal)" for p in items)
    total = sum(p["calories"] for p in items)
    return {
        "ok": True,
        "result": (
            f"Fine — overruled and logged anyway: {food_summary} = ~{total} cal. "
            f"Marked as overruled, dashboard's keeping score."
        ),
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
        return {"ok": True, "result": "You haven't eaten anything today.", "side_effects": []}

    if remaining > 0:
        result = f"You've eaten {total}/{target} calories today ({count} meals). Still have {remaining} left."
    else:
        over = abs(remaining)
        result = f"You've eaten {total} calories today — that's {over} OVER your {target} target ({count} meals)."

    return {"ok": True, "result": result, "side_effects": []}


async def handle_callback(action: str, item_id: str) -> dict | None:
    storage = _get_storage()
    if action == "delete":
        if storage.delete_meal(item_id):
            return {"message": "Meal deleted.", "refresh": True, "refresh_function": "cmd_meals"}
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
        return {"text": "No meals logged today.", "inline_keyboard": None}

    pct = round((total / target) * 100) if target > 0 else 0
    bar = "#" * int(pct / 10) + "-" * (10 - int(pct / 10))

    lines = [
        "Calories Today",
        f"[{bar}] {pct}%",
        f"Eaten: {total} / {target} cal",
    ]
    if remaining > 0:
        lines.append(f"Remaining: {remaining} cal")
    else:
        lines.append(f"Over by: {abs(remaining)} cal")
    lines.append(f"Meals: {count}")
    lines.append(f"P: {stats['total_protein']}g | C: {stats['total_carbs']}g | F: {stats['total_fat']}g")

    return {"text": "\n".join(lines), "inline_keyboard": None}


async def cmd_meals(args: str = "") -> dict:
    """Show today's meals with delete buttons. Marks overruled entries."""
    storage = _get_storage()
    meals = storage.get_today_meals()
    if not meals:
        return {"text": "No meals today.", "inline_keyboard": None}

    lines = []
    rows = []
    for i, m in enumerate(meals, 1):
        cal = f" ({m['calories']} cal)" if m["calories"] > 0 else ""
        flag = " [overruled]" if m.get("status") == "overruled" else ""
        lines.append(f"{i}. {m['food_name']}{cal} [{m['meal_type']}]{flag}")
        rows.append([
            {"text": f"x {m['food_name'][:15]}", "callback_data": f"plugin:calorie:delete:{m['id']}"},
        ])

    text = f"Today's Meals ({len(meals)}):\n" + "\n".join(lines)
    return {"text": text, "inline_keyboard": rows}


async def cmd_set_target(args: str = "") -> dict:
    """Set daily calorie target."""
    storage = _get_storage()
    if args:
        try:
            target = int(args.strip())
            storage.set_daily_target(target)
            return {"text": f"Daily target set to {target} calories.", "inline_keyboard": None}
        except ValueError:
            return {"text": "Invalid number. Use: /p.calories.target 2000", "inline_keyboard": None}

    current = storage.get_daily_target()
    return {"text": f"Current target: {current} cal\nReply with new target: /p.calories.target 2000", "inline_keyboard": None}


async def cmd_set_diet(args: str = "") -> dict:
    """View or set the active diet plan (free-text). Empty arg = view, 'off' = clear.

    The plan is sent to the LLM on every check_meal call to judge alignment with
    the intended food. Keep it short and concrete (1-2 sentences).
    """
    storage = _get_storage()
    args = args.strip()
    if not args:
        current = storage.get_setting("diet_plan") or ""
        if current:
            return {"text": f"Current diet plan:\n{current}\n\nReply with new plan to update, or 'off' to clear.",
                    "inline_keyboard": None}
        return {"text": "No diet plan set. Reply with /p.calories.diet <plan> to set one.\n"
                        "Example: /p.calories.diet half dieting, prefer high protein, avoid fried/sugary",
                "inline_keyboard": None}
    if args.lower() == "off":
        storage.clear_setting("diet_plan")
        return {"text": "Diet plan cleared. check_meal will only judge by calorie budget now.",
                "inline_keyboard": None}
    storage.set_setting("diet_plan", args)
    return {"text": f"Diet plan set:\n{args}\n\ncheck_meal will now factor this in.",
            "inline_keyboard": None}
