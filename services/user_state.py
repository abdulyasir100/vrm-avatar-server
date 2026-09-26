"""User state cache — physical/mental snapshot derived from recent conversation.

Background loops and chat all consult a shared cached state so nag prompts
adapt their tone instead of being hard-coded angry. State is re-derived by
a small Claude CLI Haiku call every ~10min, on demand when dirty, and on
hard-expiry. Persisted to disk so restarts don't lose context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from services import brain, sleep
from services import llm_json

logger = logging.getLogger(__name__)

_STATE_PATH = Path(config.USER_STATE_PATH)
_MEMORY_DB = config.MEMORY_DB_PATH

REFRESH_INTERVAL_SECONDS = 600   # 10 min idle refresh cadence
HARD_MAX_AGE_SECONDS = 1800      # force refresh on read if older than this
MIN_REFRESH_GAP_SECONDS = 90     # don't re-derive faster than this even if dirty
HISTORY_WINDOW_MINUTES = 60      # how much chat we feed Haiku
HISTORY_MAX_TURNS = 40

_DEFAULT_STATE: dict = {
    "physical": "fit",
    "mental": "normal",
    "energy": "normal",
    "context": "no recent signal",
    "updated_at": 0.0,
    "expires_at": 0.0,
}

_state: dict = dict(_DEFAULT_STATE)
_dirty: bool = True   # force first derivation
_last_attempt_at: float = 0.0
_lock = asyncio.Lock()


# ─── public API ─────────────────────────────────────────────────────────────


def mark_dirty() -> None:
    """Flag state for re-derivation on next refresh tick (or next read past TTL)."""
    global _dirty
    _dirty = True


def get_state() -> dict:
    """Return the current cached state dict. Never blocks."""
    return dict(_state)


_JOURNAL_KIND = "user_state"
_PEER_STATE_HOURS = 3  # how long another character's read of him stays worth mentioning
_SIGNAL_KEYS = ("physical", "mental", "energy", "context")
_last_shared: tuple | None = None


def _is_default(state: dict) -> bool:
    signal = (state.get("physical", "fit"), state.get("mental", "normal"), state.get("energy", "normal"))
    return signal == ("fit", "normal", "normal")


def _share(state: dict) -> None:
    """Journal a changed, non-default state so the other characters see how he is doing."""
    global _last_shared
    signal = tuple(state.get(k) for k in _SIGNAL_KEYS)
    if _is_default(state) or signal == _last_shared:
        return
    _last_shared = signal
    brain.journal_log(_JOURNAL_KIND, json.dumps(dict(zip(_SIGNAL_KEYS, signal))))


def _peer_state_block() -> str:
    """What another character last noticed — he may have told them, not this one."""
    for entry in brain.journal_from_others(_JOURNAL_KIND, _PEER_STATE_HOURS, limit=1):
        try:
            seen = json.loads(entry["content"])
        except ValueError:
            continue
        return (
            f"## User State seen by {entry['author']} ({brain.age_label(entry['age_minutes'])})\n"
            f"Physical: {seen.get('physical')} | Mental: {seen.get('mental')} | Energy: {seen.get('energy')}\n"
            f"Why: {seen.get('context', '')}\n"
            f"He told {entry['author']}, not you — let it soften or sharpen your tone, and you may "
            f"mention that {entry['author']} said so."
        )
    return ""


def format_for_prompt() -> str:
    """System-prompt block for the user's current state: this character's own read, plus
    another character's recent one. Empty when there is no signal, to keep the prompt lean."""
    return "\n\n".join(b for b in (_own_state_block(), _peer_state_block()) if b)


def _own_state_block() -> str:
    if not _state.get("updated_at"):
        return ""
    physical = _state.get("physical", "fit")
    mental = _state.get("mental", "normal")
    energy = _state.get("energy", "normal")
    ctx = _state.get("context", "")
    if _is_default(_state):
        return ""

    age_min = int((time.time() - _state.get("updated_at", time.time())) / 60)
    return (
        "## User State (live)\n"
        f"Physical: {physical} | Mental: {mental} | Energy: {energy}\n"
        f"Why: {ctx}\n"
        f"(Updated {age_min}m ago.)\n"
        "Use this as background colour for any reminder, nag, or idle remark. "
        "Tools still fire on their normal triggers — only adjust *tone*. "
        "If physical state is sick/tired, ease off gym/step/workout pushes; "
        "if mental is sad/stressed, dial back roast intensity. "
        "Stay fully in character otherwise — Facebook scrolling, teeth, and "
        "broken promises are still fair game."
    )


# ─── persistence ────────────────────────────────────────────────────────────


def load_from_disk() -> None:
    """Read last-known state from disk on startup."""
    global _state, _dirty
    try:
        raw = _STATE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        for key in _DEFAULT_STATE:
            if key not in data:
                data[key] = _DEFAULT_STATE[key]
        _state = data
        if time.time() > _state.get("expires_at", 0):
            _dirty = True
        logger.info(
            f"[user_state] Loaded from disk: physical={_state['physical']} "
            f"mental={_state['mental']} energy={_state['energy']}"
        )
    except FileNotFoundError:
        logger.info("[user_state] No persisted state yet; starting from defaults")
    except Exception as e:
        logger.warning(f"[user_state] Disk load failed: {e!r}; using defaults")


def _save_to_disk() -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(_state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[user_state] Disk save failed: {e!r}")


# ─── derivation ─────────────────────────────────────────────────────────────


def _recent_conversation() -> list[dict]:
    """Pull last HISTORY_WINDOW_MINUTES of chat from memory.db."""
    cutoff = (
        datetime.utcnow() - timedelta(minutes=HISTORY_WINDOW_MINUTES)
    ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with sqlite3.connect(_MEMORY_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT timestamp, role, content FROM conversations "
                "WHERE timestamp > ? ORDER BY id DESC LIMIT ?",
                (cutoff, HISTORY_MAX_TURNS),
            ).fetchall()
    except Exception as e:
        logger.warning(f"[user_state] memory.db read failed: {e!r}")
        return []
    return [
        {"timestamp": r["timestamp"], "role": r["role"], "content": r["content"]}
        for r in reversed(rows)
    ]


_INSTRUCTION = (
    "You read a short conversation excerpt and infer the user's current state. "
    "Reply with EXACTLY one JSON object, no markdown, no commentary.\n\n"
    "Schema:\n"
    "{\n"
    '  "physical": "fit" | "tired" | "sick",\n'
    '  "mental":   "normal" | "happy" | "sad" | "stressed" | "busy" | "affectionate" | "playful" | "angry",\n'
    '  "energy":   "low" | "normal" | "high",\n'
    '  "context":  "one sentence explaining the call, with approximate times"\n'
    "}\n\n"
    "Guidance:\n"
    '- "sick" if user mentioned illness/symptoms in the last 6h and has NOT said they feel better since\n'
    '- "tired" if user mentioned exhaustion, no sleep, long session, or wanting to nap\n'
    '- "busy" if user said they\'re working, on a call, or asked to be left alone\n'
    '- "sad" / "stressed" if user expressed real low mood, grief, anxiety, frustration at life (not at the assistant)\n'
    '- "affectionate" if recent exchanges have love-words, "babe/ily", emotional warmth — even with light teasing\n'
    '- "angry" only if user is actually angry at YOU (the assistant) right now\n'
    "- Default is fit + normal + normal — only deviate when there is a clear signal.\n"
    "- The context field should reference real timestamps from the excerpt."
)


def _build_prompt(history: list[dict]) -> str:
    if not history:
        return (
            _INSTRUCTION
            + "\n\nConversation excerpt:\n(no recent messages)\n\n"
            + 'Return the default state with context "no recent signal".'
        )
    lines = []
    for row in history:
        snippet = row["content"].replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        lines.append(f'[{row["timestamp"]}] {row["role"]}: {snippet}')
    return _INSTRUCTION + "\n\nConversation excerpt:\n" + "\n".join(lines)


def _normalize(data: dict) -> dict:
    """The model's JSON object -> the state keys, missing ones defaulted."""
    out = {}
    for key, default in _DEFAULT_STATE.items():
        if key in {"updated_at", "expires_at"}:
            continue
        val = data.get(key, default)
        if isinstance(val, str):
            val = val.strip().lower() if key != "context" else val.strip()
        out[key] = val
    return out


async def derive_now() -> dict | None:
    """Run the LLM call and update the cached state. Returns the new state
    on success, None on failure (state untouched)."""
    global _state, _dirty, _last_attempt_at

    async with _lock:
        _last_attempt_at = time.time()
        history = _recent_conversation()
        prompt = _build_prompt(history)
        # Claude CLI (haiku) when Claude is the provider, else the configured OpenAI-compatible
        # model, then the fallback chain — so this works on any provider.
        data = await llm_json.ask_json(None, prompt, timeout=45)
        if not data:
            logger.warning("[user_state] No parseable state from any LLM")
            return None
        parsed = _normalize(data)

        now = time.time()
        _state = {
            **parsed,
            "updated_at": now,
            "expires_at": now + HARD_MAX_AGE_SECONDS,
        }
        _dirty = False
        _save_to_disk()
        await asyncio.to_thread(_share, _state)
        logger.info(
            f"[user_state] Updated: physical={_state['physical']} "
            f"mental={_state['mental']} energy={_state['energy']} "
            f"context={_state['context']!r:.120}"
        )
        return dict(_state)


# ─── background loop ────────────────────────────────────────────────────────


async def refresh_loop() -> None:
    """Background task: refresh state when dirty, on TTL, or every interval.

    Started from main.py startup. Survives one-off failures.
    """
    load_from_disk()
    # Tiny grace period before first derivation so startup isn't gated on it
    await asyncio.sleep(15)
    while True:
        try:
            now = time.time()
            age = now - _state.get("updated_at", 0)
            elapsed_since_attempt = now - _last_attempt_at
            dirty_due = _dirty and elapsed_since_attempt > MIN_REFRESH_GAP_SECONDS
            # Asleep: nothing new to read, so only a real chat (dirty) re-derives.
            if sleep.is_sleeping():
                should_refresh = dirty_due
            else:
                should_refresh = (
                    dirty_due
                    or age > HARD_MAX_AGE_SECONDS
                    or age > REFRESH_INTERVAL_SECONDS
                )
            if should_refresh:
                await derive_now()
        except Exception as e:
            logger.warning(f"[user_state] refresh tick failed: {e!r}")
        await asyncio.sleep(60)
