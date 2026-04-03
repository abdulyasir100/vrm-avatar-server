"""Todo plugin — idle talk prompts."""

import logging

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Todo plugin storage not initialized")
    return _storage


def get_idle_prompts() -> list[str]:
    """Return idle talk prompts based on current task state."""
    try:
        storage = _get_storage()
        overdue = storage.get_overdue()
        active = storage.get_all(completed=False)
    except Exception:
        return []

    prompts = []

    if overdue:
        titles = ", ".join(t["title"] for t in overdue[:3])
        count = len(overdue)
        prompts.append(
            f"The user has {count} overdue task(s): {titles}. "
            f"Nag them about it naturally — be in-character (teasing but caring). One sentence."
        )
    elif active:
        titles = ", ".join(t["title"] for t in active[:3])
        prompts.append(
            f"The user has these upcoming tasks: {titles}. "
            f"Mention one casually — like you're keeping track for them. One sentence."
        )
    else:
        prompts.append(
            "The user has no tasks on their to-do list right now. "
            "Tease them about being lazy or suggest they add something. One sentence."
        )

    return prompts
