"""Money plugin — idle talk prompts."""

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def get_idle_prompts() -> list[str]:
    if _storage is None:
        return []
    try:
        status = _storage.get_spending_status()
        expense = status.get("monthly_expense", 0)
        percent = status.get("spent_percent", 0)
        budget = status.get("monthly_budget", 0)

        if expense <= 0:
            return []

        formatted = f"Rp {expense:,}".replace(",", ".")
        if budget > 0:
            return [
                f"The user has spent {formatted} this month ({int(percent)}% of budget). "
                f"Comment on their spending casually — not a warning, just an observation. One sentence."
            ]
        else:
            return [
                f"The user has spent {formatted} this month. "
                f"Comment on it casually. One sentence."
            ]
    except Exception:
        return []
