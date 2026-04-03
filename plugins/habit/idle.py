"""Habit tracker idle talk prompts."""

import logging

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Habit storage not initialized")
    return _storage


def get_idle_prompts() -> list[str]:
    try:
        storage = _get_storage()
        habits = storage.get_all_with_status()
    except Exception:
        return []

    if not habits:
        return []

    prompts = []
    incomplete = [h for h in habits if not h["done_today"]]
    streaks = [h for h in habits if h["streak"] >= 3]

    if incomplete:
        names = ", ".join(h["name"] for h in incomplete[:3])
        prompts.append(
            f"The user hasn't done these habits today: {names}. "
            f"Nag them playfully — be motivating but teasing. One sentence."
        )

    if streaks:
        best = max(streaks, key=lambda h: h["streak"])
        prompts.append(
            f"The user has a {best['streak']}-day streak on '{best['name']}'. "
            f"Mention it positively — be impressed or encourage them to keep going. One sentence."
        )

    return prompts
