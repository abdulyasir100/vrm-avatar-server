"""Capability summary — a compact, always-on list of what the character can do.

Injected into every chat system prompt so the character has *holistic*
awareness of her own features (so "what can you do?" is grounded and she can
proactively offer them). This is intentionally NOT the full tool docs — those
are injected reactively per-message by services/tool_router.py. This block is
just a short menu of capabilities.

Derived entirely from plugin manifests + the known main-feature set, so it
stays in sync as plugins are added/removed. Each plugin owns its phrasing via
an optional manifest "summary" field (falls back to "description"). No
character-specific strings here — consistent with the project's no-hardcode
rule.
"""

from __future__ import annotations

import logging

from services import plugin_loader

logger = logging.getLogger(__name__)

# Main/Unity features that live in services/tools/ rather than as plugins, plus
# built-ins. These have no manifest, so they're listed here. Short phrases.
_MAIN_FEATURE_PHRASES = [
    "swap your costume",
    "gacha / roulette / THR games",
    "web search (smart mode)",
    "long-term memory (remember facts about him)",
]

_cached: str | None = None


def _plugin_phrase(manifest: dict) -> str:
    """One short capability phrase for a plugin: prefer `summary`, else `description`."""
    phrase = (manifest.get("summary") or manifest.get("description") or manifest.get("name") or "").strip()
    # Keep it compact — clip an over-long description at the first sentence/dash.
    for sep in (" — ", ". ", " - "):
        if sep in phrase:
            phrase = phrase.split(sep, 1)[0].strip()
            break
    return phrase


def build_capability_summary() -> str:
    """Compact '## YOUR CAPABILITIES' block. Built once and cached."""
    global _cached
    if _cached is not None:
        return _cached

    phrases: list[str] = []
    notes: list[str] = []
    try:
        for entry in plugin_loader.get_enabled_plugins().values():
            manifest = entry["manifest"]
            phrase = _plugin_phrase(manifest)
            if phrase:
                phrases.append(phrase)
            # Optional always-on fact a plugin wants the character to never
            # forget (e.g. "/play launches the game Mini App"). Generic field —
            # no plugin is hardcoded here.
            note = (manifest.get("capability_note") or "").strip()
            if note:
                notes.append(note)
    except Exception as e:
        logger.warning(f"[capabilities] plugin enumeration failed: {e}")

    phrases.extend(_MAIN_FEATURE_PHRASES)

    items = "; ".join(phrases)
    block = (
        "\n\n## YOUR CAPABILITIES\n"
        "Beyond chatting, you can help with: " + items + ". "
        "These are real features wired to tools — offer them naturally when the "
        "moment fits, but don't recite the whole list robotically."
    )
    if notes:
        block += "\n\nAlways remember:\n" + "\n".join(f"- {n}" for n in notes)
    _cached = block
    return _cached


def reset_cache() -> None:
    """Clear the cache (e.g. after plugins are reloaded)."""
    global _cached
    _cached = None
