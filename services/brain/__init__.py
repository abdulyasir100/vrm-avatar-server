"""The second brain: what every character knows about the owner.

Two stores, one backend:
  facts   — durable knowledge. `services.memory` is the only caller: it routes shared
            categories here and keeps `relationship` in the character's own memory.db.
  journal — dated notes that age out (session summaries, his current state). Written and
            read through journal_log / journal_from_others below, which never raise.

Backends (config.BRAIN_BACKEND):
  sqlite   — local file, the clone-friendly default
  postgres — the server's shared brain (BRAIN_DSN); psycopg is imported lazily
"""

import logging
import time

import config
from services.brain.base import ID_BASE, BrainBackend

logger = logging.getLogger(__name__)

# Knowledge about the owner is shared, and every row names who learned it. Only how a
# character relates to him stays that character's own (with its raw chat log and mood).
SHARED_CATEGORIES = frozenset({"fact", "preference", "event"})

_backend: BrainBackend | None = None


def is_shared_category(category: str | None) -> bool:
    return category in SHARED_CATEGORIES


def is_brain_id(memory_id: int) -> bool:
    return memory_id > ID_BASE


def get() -> BrainBackend:
    """The configured backend, built on first use. Raises if it cannot be reached."""
    global _backend
    if _backend is None:
        if config.BRAIN_BACKEND == "postgres":
            from services.brain.postgres_backend import PostgresBrain
            backend = PostgresBrain(config.BRAIN_DSN)
        else:
            from services.brain.sqlite_backend import SqliteBrain
            backend = SqliteBrain(config.BRAIN_DB_PATH)
        logger.info(f"[brain] backend: {config.BRAIN_BACKEND} ({backend.count()} facts)")
        _backend = backend
    return _backend


def reset() -> None:
    """Drop the cached backend (tests, config change)."""
    global _backend
    _backend = None
    _journal_cache.clear()


# --- journal: never raises — a missing brain must not cost a chat turn ---------

def journal_log(kind: str, content: str) -> None:
    """Record something the other characters should know about (as this character)."""
    try:
        get().log(kind, content, config.CHARACTER_NAME)
    except Exception as e:
        logger.warning(f"[brain] journal write failed ({kind}): {e}")


_JOURNAL_CACHE_SECONDS = 20  # every prompt build asks; the brain may be a network hop away
_journal_cache: dict[tuple, tuple[float, list[dict]]] = {}


def journal_from_others(kind: str, hours: float, limit: int = 10) -> list[dict]:
    """Recent entries written by anyone but this character, newest first."""
    key = (kind, hours, limit)
    cached = _journal_cache.get(key)
    if cached and time.time() - cached[0] < _JOURNAL_CACHE_SECONDS:
        return cached[1]
    try:
        rows = get().recent(kind, hours, limit * 3)
    except Exception as e:
        logger.warning(f"[brain] journal read failed ({kind}): {e}")
        rows = cached[1] if cached else []
    mine = config.CHARACTER_NAME
    _journal_cache[key] = (time.time(), [r for r in rows if r["author"] != mine][:limit])
    return _journal_cache[key][1]


def age_label(minutes: int) -> str:
    return f"{minutes}m ago" if minutes < 90 else f"{minutes // 60}h ago" if minutes < 2880 else f"{minutes // 1440}d ago"
