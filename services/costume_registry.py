"""Costume registry with tag-based fuzzy matching.

Loads costume definitions from data/costumes.json.
Resolves natural language queries to the best-matching costume via tag overlap.
"""

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

_costumes: dict[str, dict] = {}  # costume_id -> {vrm, display_name, enabled, tags}
_current_costume: str = "maid"


def load(path: str = "data/costumes.json") -> None:
    """Load costume definitions from JSON."""
    global _costumes
    p = Path(path)
    if not p.exists():
        logger.warning(f"Costumes file not found: {path}")
        return
    _costumes = json.loads(p.read_text(encoding="utf-8"))
    enabled = [k for k, v in _costumes.items() if v.get("enabled", True)]
    logger.info(f"[costumes] Loaded {len(_costumes)} costumes, {len(enabled)} enabled: {enabled}")


def get_all() -> dict[str, dict]:
    """Return all costume definitions."""
    return _costumes


def get_enabled() -> dict[str, dict]:
    """Return only enabled costumes."""
    return {k: v for k, v in _costumes.items() if v.get("enabled", True)}


def get(costume_id: str) -> dict | None:
    """Get a costume by exact ID."""
    return _costumes.get(costume_id)


def get_current() -> str:
    """Return current costume ID."""
    return _current_costume


def set_current(costume_id: str) -> None:
    """Update the current costume state."""
    global _current_costume
    if costume_id in _costumes:
        _current_costume = costume_id
        logger.info(f"[costumes] Current costume set to '{costume_id}'")


def resolve(query: str) -> str | None:
    """Resolve a natural language query to the best matching enabled costume.

    1. Exact ID match (e.g. "maid" -> maid)
    2. Tag overlap scoring (e.g. "favorite" -> maid, "comfy" -> casual)
    3. Returns None if no match found

    Special values:
    - "random" -> random enabled costume (different from current)
    """
    query_lower = query.strip().lower()
    enabled = get_enabled()

    if not enabled:
        return None

    if query_lower == "random":
        options = [k for k in enabled if k != _current_costume]
        return random.choice(options) if options else _current_costume

    if query_lower in enabled:
        return query_lower

    query_tokens = set(query_lower.split())
    scores: list[tuple[str, int]] = []

    for costume_id, info in enabled.items():
        tags = set(info.get("tags", []))
        overlap = len(query_tokens & tags)
        if overlap > 0:
            scores.append((costume_id, overlap))

    if scores:
        scores.sort(key=lambda x: x[1], reverse=True)
        best_id, best_score = scores[0]
        logger.info(f"[costumes] Tag match: '{query}' -> {best_id} (score: {best_score})")
        return best_id

    return None


def list_tags() -> dict[str, list[str]]:
    """Return all costume tags grouped by costume ID (enabled only)."""
    return {k: v.get("tags", []) for k, v in get_enabled().items()}
