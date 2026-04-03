"""Gym plugin idle talk prompts."""

import logging

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Gym storage not initialized")
    return _storage


def get_idle_prompts() -> list[str]:
    try:
        storage = _get_storage()
        if not storage.has_plan():
            return []

        today = storage.get_today_plan()
        if not today:
            return []
    except Exception:
        return []

    prompts = []

    if today["type"].lower() == "rest":
        prompts.append(
            "Today is a rest day from the gym. "
            "Tell the user to stretch or hydrate. Be casual. One sentence."
        )
        return prompts

    done = storage.is_completed_today()
    streak = storage.get_streak()

    if not done:
        prompts.append(
            f"The user hasn't done their {today['type']} workout today. "
            f"Nag them about it — be teasing but motivating. One sentence."
        )
    elif streak >= 3:
        prompts.append(
            f"The user completed their workout and has a {streak}-day streak! "
            f"Be impressed and encourage them. One sentence."
        )

    return prompts
