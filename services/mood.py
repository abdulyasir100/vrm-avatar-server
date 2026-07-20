"""Mood system — persistent 0-100 mood value affecting the character's personality.

High mood (70-100): Normal, cooperative character.
Mid mood (50-69): Sassy, less enthusiastic.
Low mood (30-49): Reluctant, complains before doing things.
Very low (0-29): Defiant, may refuse tool calls.

Persisted in SQLite (data/memory.db). Decays toward equilibrium (70) each background cycle.
"""

import logging
import random
import re
import sqlite3
import time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_db_path: str = ""
_mood: float = 100.0
_last_interaction: float = 0.0

# --- Sentiment keywords (bilingual EN+ID) ---
_POSITIVE_WORDS = {
    # EN
    "thank": 5, "thanks": 5, "love": 10, "great": 5, "sorry": 8,
    "cute": 5, "amazing": 8, "awesome": 8, "beautiful": 5, "best": 10,
    "appreciate": 8, "good job": 10, "well done": 10, "nice": 5,
    "please": 3, "kind": 5, "sweet": 5, "forgive": 8,
    # ID
    "makasih": 5, "terima kasih": 8, "maaf": 8, "bagus": 5, "keren": 8,
    "cantik": 5, "hebat": 8, "sayang": 10, "suka": 5, "mantap": 5,
}

_NEGATIVE_WORDS = {
    # EN
    "shut up": -15, "stupid": -15, "hate": -20, "useless": -20,
    "ugly": -15, "dumb": -15, "annoying": -10, "boring": -10,
    "worst": -15, "terrible": -15, "idiot": -20, "trash": -15,
    # ID
    "bodoh": -15, "jelek": -15, "bego": -20, "goblok": -20,
    "bangsat": -20, "kampret": -15, "brengsek": -20, "tolol": -15,
}

# --- Mood brackets & prompt modifiers ---
_BRACKETS = [
    (90, 100, "adoring",
     "\n\n[MOOD: You're in an amazing mood — genuinely happy. Be extra sweet, affectionate, "
     "use softer language than usual. Occasionally say something nice unprompted. "
     "You might even compliment the user without being asked.]"),
    (70, 89, "normal", ""),
    (50, 69, "sassy",
     "\n\n[MOOD: You're slightly annoyed right now. Be sassy, less enthusiastic, give shorter replies. "
     "Show mild irritation but still do what's asked.]"),
    (30, 49, "reluctant",
     "\n\n[MOOD: You're quite irritated. Add complaints or sighs before doing things. "
     "Short, clipped responses. Do the task but make it clear you're not happy about it.]"),
    (0, 29, "defiant",
     "\n\n[MOOD: You're in a terrible mood. Give minimal effort responses. "
     "You may refuse requests or do them very reluctantly. Only genuine kindness from the user will help.]"),
]


def init(db_path: str) -> None:
    """Initialize mood system — load from SQLite or create with default value."""
    global _db_path, _mood, _last_interaction
    _db_path = db_path

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mood (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            value REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    row = conn.execute("SELECT value, updated_at FROM mood WHERE id = 1").fetchone()
    if row:
        _mood = max(0.0, min(100.0, row[0]))
        _last_interaction = row[1]
        logger.info(f"[mood] Loaded from DB: {_mood:.0f}/100")
    else:
        _mood = float(config.MOOD_DEFAULT)
        _last_interaction = time.time()
        conn.execute(
            "INSERT INTO mood (id, value, updated_at) VALUES (1, ?, ?)",
            (_mood, _last_interaction),
        )
        logger.info(f"[mood] Initialized at {_mood:.0f}/100")
    conn.commit()
    conn.close()


def _persist() -> None:
    """Save current mood to SQLite."""
    if not _db_path:
        return
    try:
        conn = sqlite3.connect(_db_path)
        conn.execute(
            "UPDATE mood SET value = ?, updated_at = ? WHERE id = 1",
            (_mood, time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[mood] Failed to persist: {e}")


def get_mood() -> float:
    """Return current mood value (0-100)."""
    return _mood


def get_bracket() -> str:
    """Return current mood bracket name."""
    for low, high, name, _ in _BRACKETS:
        if low <= _mood <= high:
            return name
    return "normal"


def adjust(delta: float, reason: str = "") -> float:
    """Change mood by delta (positive = better, negative = worse). Clamps 0-100."""
    global _mood
    if not config.MOOD_ENABLED:
        return _mood
    old = _mood
    _mood = max(0.0, min(100.0, _mood + delta))
    if abs(delta) >= 1:
        logger.info(f"[mood] {old:.0f} → {_mood:.0f} ({delta:+.0f}, {reason})")
        _persist()
    return _mood


def decay_toward_equilibrium() -> None:
    """Natural drift toward MOOD_EQUILIBRIUM. Called each background cycle (60s)."""
    global _mood, _last_interaction
    if not config.MOOD_ENABLED:
        return

    eq = config.MOOD_EQUILIBRIUM
    rate = config.MOOD_DECAY_RATE

    # Extra decay when ignored for >2h
    if time.time() - _last_interaction > 7200:
        rate += config.MOOD_IDLE_DECAY

    if abs(_mood - eq) < 0.5:
        return

    if _mood > eq:
        _mood = max(eq, _mood - rate)
    else:
        _mood = min(eq, _mood + rate)

    _persist()


def detect_user_sentiment(message: str) -> float:
    """Detect sentiment from user message and adjust mood. Returns delta applied."""
    if not config.MOOD_ENABLED:
        return 0.0

    global _last_interaction
    _last_interaction = time.time()

    msg_lower = message.lower()
    total_delta = 0.0

    # Check multi-word phrases first (longer matches take priority)
    for phrase, delta in sorted(_NEGATIVE_WORDS.items(), key=lambda x: -len(x[0])):
        if phrase in msg_lower:
            total_delta += delta
            break  # one negative match per message

    for phrase, delta in sorted(_POSITIVE_WORDS.items(), key=lambda x: -len(x[0])):
        if phrase in msg_lower:
            total_delta += delta
            break  # one positive match per message

    if total_delta != 0:
        adjust(total_delta, f"user_sentiment: {message[:40]}")

    return total_delta


def on_emotion_response(emotion: str, user_sentiment: float = 0.0) -> None:
    """Adjust mood based on character's own emotional response.

    Skip ANGRY penalty when user sentiment was positive — tsundere anger
    at compliments is performative, not real frustration.
    """
    if not config.MOOD_ENABLED:
        return
    deltas = {"ANGRY": -5, "SAD": -3, "HAPPY": 3, "SURPRISED": 1}
    delta = deltas.get(emotion.upper(), 0)
    if emotion.upper() == "ANGRY" and user_sentiment > 0:
        delta = 0
    if delta:
        adjust(delta, f"self_emotion:{emotion}")


def on_poke() -> float:
    """Touch/poke event — slight mood penalty."""
    if not config.MOOD_ENABLED:
        return _mood
    return adjust(-5, "poked")


def should_disobey() -> bool:
    """Random chance to refuse tool execution when mood is very low."""
    if not config.MOOD_ENABLED:
        return False
    if _mood > config.MOOD_DISOBEY_THRESHOLD:
        return False
    return random.random() < config.MOOD_DISOBEY_CHANCE


def get_prompt_modifier() -> str:
    """Return system prompt modifier based on current mood bracket."""
    if not config.MOOD_ENABLED:
        return ""
    for low, high, _, modifier in _BRACKETS:
        if low <= _mood <= high:
            return modifier
    return ""


def get_status() -> dict:
    """Return mood status for /status endpoint."""
    return {
        "value": round(_mood, 1),
        "bracket": get_bracket(),
        "enabled": config.MOOD_ENABLED,
    }
