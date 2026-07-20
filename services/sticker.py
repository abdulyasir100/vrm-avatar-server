"""Sticker service — tag-based sticker matching for Telegram.

Loads sticker definitions from data/stickers.json.
Resolves reply text + emotion to the best-matching sticker via tag overlap.
"""

import json
import logging
import random
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_stickers: list[dict] = []  # [{id, file_id, emotion, tags}, ...]


def load(path: str | None = None) -> None:
    """Load sticker definitions from JSON. Filters out entries with empty file_id."""
    global _stickers
    p = Path(path or config.STICKER_MAP_PATH)
    if not p.exists():
        example = p.with_name(p.stem + ".example" + p.suffix)
        if example.exists():
            logger.info(f"[sticker] {p.name} not found, using {example.name}")
            p = example
        else:
            logger.warning(f"[sticker] Sticker file not found: {p}")
            return
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        _stickers = [s for s in raw if s.get("file_id")]
        logger.info(f"[sticker] Loaded {len(_stickers)} stickers (skipped {len(raw) - len(_stickers)} without file_id)")
    except Exception as e:
        logger.error(f"[sticker] Failed to load stickers: {e}")


def resolve(reply_text: str, emotion: str) -> str | None:
    """Resolve reply text + emotion to a sticker file_id.

    Two-tier selection:
      1. Tokenize reply, score tag overlap → pick best match
      2. If no tag match → random sticker matching the emotion
    """
    if not _stickers:
        return None

    # Tier 1: tag overlap
    tokens = set(reply_text.lower().split())
    scores: list[tuple[dict, int]] = []
    for s in _stickers:
        tags = set(s.get("tags", []))
        overlap = len(tokens & tags)
        if overlap > 0:
            scores.append((s, overlap))

    if scores:
        scores.sort(key=lambda x: x[1], reverse=True)
        best = scores[0][0]
        logger.debug(f"[sticker] Tag match: {best['id']} (score: {scores[0][1]})")
        return best["file_id"]

    # Tier 2: emotion fallback
    emotion_matches = [s for s in _stickers if s.get("emotion") == emotion]
    if emotion_matches:
        pick = random.choice(emotion_matches)
        logger.debug(f"[sticker] Emotion fallback: {pick['id']} ({emotion})")
        return pick["file_id"]

    return None


def get_sticker(emotion: str) -> str | None:
    """Get a random sticker file_id for a specific emotion (e.g. SLEEP)."""
    matches = [s for s in _stickers if s.get("emotion") == emotion]
    if matches:
        return random.choice(matches)["file_id"]
    return None


def get_all() -> list[dict]:
    """Return the full sticker list."""
    return _stickers
