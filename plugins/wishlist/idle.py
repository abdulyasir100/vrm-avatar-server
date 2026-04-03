"""Wishlist idle talk prompts."""

import logging

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def _get_storage():
    if _storage is None:
        raise RuntimeError("Wishlist storage not initialized")
    return _storage


def get_idle_prompts() -> list[str]:
    try:
        storage = _get_storage()
        items = storage.get_all()
    except Exception:
        return []

    if not items:
        return []

    prompts = []
    total = storage.get_total()

    if total > 0:
        top = items[0]
        prompts.append(
            f"The user has {len(items)} items on their wishlist totaling {total:,.0f}. "
            f"The top item is '{top['name']}'. "
            f"Tease them about it or ask if they're saving up. One sentence."
        )

    return prompts
