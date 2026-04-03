"""Example plugin idle talk prompts.

Return a list of prompt strings. One will be randomly selected during idle talk.
Keep prompts to one sentence instructions for the LLM.
"""

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def get_idle_prompts() -> list[str]:
    """Return idle talk prompts based on current plugin data state."""
    if _storage is None:
        return []
    # Query your storage and build contextual prompts
    # Example:
    # items = _storage.get_all()
    # if items:
    #     return [f"The user has {len(items)} items. Comment casually. One sentence."]
    return []
