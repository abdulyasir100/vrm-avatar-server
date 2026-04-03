"""Calorie plugin — idle talk prompts."""

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def get_idle_prompts() -> list[str]:
    if _storage is None:
        return []
    try:
        status = _storage.get_calorie_status()
        total = status.get("total_eaten", 0)
        target = status.get("daily_target", 2000)
        percent = status.get("eaten_percent", 0)

        if total > 0:
            return [
                f"The user has eaten {total} calories today ({int(percent)}% of {target} target). "
                f"Comment on their eating habits casually. One sentence."
            ]
        else:
            return [
                "The user hasn't logged any meals today. "
                "Tease them about not eating or remind them to log their food. One sentence."
            ]
    except Exception:
        return []
