"""Workout plan generator — creates a weekly plan based on profile."""

# Exercise database: home-friendly exercises with dumbbells, bodyweight, mini stepper
# Categorized by muscle group focus

EXERCISES = {
    "chest": [
        {"name": "Dumbbell Floor Press", "sets": 3, "reps": "12"},
        {"name": "Push-ups", "sets": 3, "reps": "15"},
        {"name": "Incline Push-ups", "sets": 3, "reps": "12"},
        {"name": "Dumbbell Fly (floor)", "sets": 3, "reps": "12"},
        {"name": "Wide Push-ups", "sets": 3, "reps": "12"},
        {"name": "Diamond Push-ups", "sets": 3, "reps": "10"},
    ],
    "back": [
        {"name": "Dumbbell Bent-over Row", "sets": 3, "reps": "12"},
        {"name": "Single Arm Row", "sets": 3, "reps": "12 each"},
        {"name": "Reverse Fly", "sets": 3, "reps": "12"},
        {"name": "Superman Hold", "sets": 3, "reps": "30s"},
        {"name": "Dumbbell Pullover", "sets": 3, "reps": "12"},
    ],
    "legs": [
        {"name": "Goblet Squat", "sets": 3, "reps": "15"},
        {"name": "Dumbbell Lunges", "sets": 3, "reps": "12 each"},
        {"name": "Romanian Deadlift", "sets": 3, "reps": "12"},
        {"name": "Calf Raises (weighted)", "sets": 3, "reps": "20"},
        {"name": "Bulgarian Split Squat", "sets": 3, "reps": "10 each"},
        {"name": "Mini Stepper", "sets": 1, "reps": "5 min"},
    ],
    "shoulders_arms": [
        {"name": "Dumbbell Shoulder Press", "sets": 3, "reps": "12"},
        {"name": "Lateral Raise", "sets": 3, "reps": "15"},
        {"name": "Front Raise", "sets": 3, "reps": "12"},
        {"name": "Bicep Curls", "sets": 3, "reps": "12"},
        {"name": "Hammer Curls", "sets": 3, "reps": "12"},
        {"name": "Tricep Kickback", "sets": 3, "reps": "12"},
        {"name": "Overhead Tricep Extension", "sets": 3, "reps": "12"},
    ],
    "abs": [
        {"name": "Crunches", "sets": 3, "reps": "20"},
        {"name": "Leg Raises", "sets": 3, "reps": "15"},
        {"name": "Bicycle Crunches", "sets": 3, "reps": "20"},
        {"name": "Plank", "sets": 3, "reps": "45s"},
        {"name": "Mountain Climbers", "sets": 3, "reps": "30s"},
        {"name": "Russian Twist", "sets": 3, "reps": "20"},
        {"name": "Flutter Kicks", "sets": 3, "reps": "30s"},
    ],
    "cardio": [
        {"name": "Mini Stepper", "sets": 1, "reps": "15 min"},
        {"name": "Jumping Jacks", "sets": 3, "reps": "30s"},
        {"name": "Burpees", "sets": 3, "reps": "10"},
        {"name": "High Knees", "sets": 3, "reps": "30s"},
    ],
}

# Workout split templates
SPLITS = {
    "4day_fat_loss": {
        "name": "4-Day Fat Loss + Abs",
        "description": "Upper/lower split with abs and cardio focus",
        "days": [
            {"day": 0, "type": "Chest + Shoulders + Abs", "groups": ["chest", "shoulders_arms", "abs"]},
            {"day": 1, "type": "Legs + Cardio", "groups": ["legs", "cardio"]},
            {"day": 2, "type": "Rest", "groups": []},
            {"day": 3, "type": "Back + Arms + Abs", "groups": ["back", "shoulders_arms", "abs"]},
            {"day": 4, "type": "Legs + Cardio", "groups": ["legs", "cardio"]},
            {"day": 5, "type": "Rest", "groups": []},
            {"day": 6, "type": "Rest", "groups": []},
        ],
    },
    "4day_muscle": {
        "name": "4-Day Muscle Focus",
        "description": "Push/pull/legs with dedicated arm day",
        "days": [
            {"day": 0, "type": "Push (Chest + Shoulders)", "groups": ["chest", "shoulders_arms"]},
            {"day": 1, "type": "Pull (Back + Biceps)", "groups": ["back", "shoulders_arms"]},
            {"day": 2, "type": "Rest", "groups": []},
            {"day": 3, "type": "Legs + Abs", "groups": ["legs", "abs"]},
            {"day": 4, "type": "Arms + Abs + Cardio", "groups": ["shoulders_arms", "abs", "cardio"]},
            {"day": 5, "type": "Rest", "groups": []},
            {"day": 6, "type": "Rest", "groups": []},
        ],
    },
    "4day_balanced": {
        "name": "4-Day Balanced",
        "description": "Even mix of strength and cardio",
        "days": [
            {"day": 0, "type": "Upper Body + Abs", "groups": ["chest", "back", "abs"]},
            {"day": 1, "type": "Lower Body + Cardio", "groups": ["legs", "cardio"]},
            {"day": 2, "type": "Rest", "groups": []},
            {"day": 3, "type": "Arms + Shoulders + Abs", "groups": ["shoulders_arms", "abs"]},
            {"day": 4, "type": "Full Body + Cardio", "groups": ["legs", "chest", "cardio"]},
            {"day": 5, "type": "Rest", "groups": []},
            {"day": 6, "type": "Rest", "groups": []},
        ],
    },
}


def generate_plan(split_key: str) -> list[dict]:
    """Generate a weekly plan from a split template.

    Returns list of 7 dicts: {day: 0-6, type: str, exercises: list[dict]}
    """
    split = SPLITS.get(split_key)
    if not split:
        return []

    plan = []
    for day_template in split["days"]:
        exercises = []
        if day_template["groups"]:
            for group in day_template["groups"]:
                group_exercises = EXERCISES.get(group, [])
                # Pick 2-3 exercises per group to keep sessions manageable
                pick = min(3, len(group_exercises))
                exercises.extend(group_exercises[:pick])

        plan.append({
            "day": day_template["day"],
            "type": day_template["type"],
            "exercises": exercises,
        })

    return plan


def format_workout(plan_day: dict, show_header: bool = True) -> str:
    """Format a single day's workout for display."""
    lines = []
    if show_header:
        lines.append(f"{plan_day['day_name']}: {plan_day['type']}")
        lines.append("")

    if plan_day["type"].lower() == "rest":
        lines.append("Rest day! Recover and stretch.")
        return "\n".join(lines)

    for i, ex in enumerate(plan_day["exercises"], 1):
        lines.append(f"{i}. {ex['name']} — {ex['sets']}x{ex['reps']}")

    return "\n".join(lines)


def format_week(plan: list[dict], completions: list[str] = None) -> str:
    """Format full weekly plan."""
    today_day = None
    try:
        from datetime import datetime, timezone, timedelta
        today_day = datetime.now(timezone(timedelta(hours=7))).weekday()
    except Exception:
        pass

    lines = []
    for d in plan:
        marker = ""
        if today_day is not None and d["day"] == today_day:
            marker = " << TODAY"

        if d["type"].lower() == "rest":
            lines.append(f"{d['day_name']}: Rest{marker}")
        else:
            ex_count = len(d["exercises"])
            lines.append(f"{d['day_name']}: {d['type']} ({ex_count} exercises){marker}")

    return "\n".join(lines)
