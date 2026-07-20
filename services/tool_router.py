"""Keyword-based tool router — selects relevant tool docs for the LLM prompt.

Instead of dumping all 50+ tool descriptions into the system prompt,
this module detects user intent via keywords and returns only the
relevant tool docs (~3-5 at most). Keeps the 8B model fast and accurate.
"""

import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_tool_docs: dict[str, str] = {}  # tool_name -> markdown doc string

_routes: list[tuple[re.Pattern, list[str], bool]] = []  # (pattern, tools, requires_intent)

def register_route(pattern: str, tool_names: list[str], requires_intent: bool = False) -> None:
    """Register a keyword pattern that activates specific tool docs.

    If requires_intent=True, the message must also have an intent prefix
    (e.g. 'sui-chan', 'please', 'add', 'check') for the route to match.
    Used by plugins to avoid triggering on casual conversation.
    """
    _routes.append((re.compile(pattern, re.IGNORECASE), tool_names, requires_intent))


def register_tool_doc(tool_name: str, doc: str) -> None:
    """Register a tool's documentation text."""
    _tool_docs[tool_name] = doc.strip()


def load_tool_docs(tools_dir: str = "prompts/tools") -> None:
    """Load tool docs from individual markdown files in prompts/tools/.

    Each file is named <tool_name>.md and contains the tool's prompt section.
    """
    tools_path = Path(tools_dir)
    if not tools_path.exists():
        logger.warning(f"Tool docs directory not found: {tools_dir}")
        return

    for md_file in sorted(tools_path.glob("*.md")):
        tool_name = md_file.stem
        doc = md_file.read_text(encoding="utf-8").strip()
        _tool_docs[tool_name] = doc
        logger.info(f"[tool_router] Loaded doc: {tool_name} ({len(doc)} chars)")


def get_relevant_tools(user_message: str) -> list[str]:
    """Return tool names relevant to the user's message."""
    from services.plugin_loader import has_intent_prefix

    has_intent = has_intent_prefix(user_message)
    matched = set()
    for pattern, tool_names, requires_intent in _routes:
        if requires_intent and not has_intent:
            continue
        if pattern.search(user_message):
            matched.update(tool_names)
    return sorted(matched)


def build_tools_prompt(user_message: str) -> str:
    """Build the TOOLS section of the system prompt with only relevant tools.

    Returns empty string if no tools match the user's message.
    Always includes the general tools header and memory tools (always active).
    """
    always_active = {"save_memory", "update_memory", "delete_memory"}

    matched = set(get_relevant_tools(user_message))
    active_tools = always_active | matched

    docs = []
    for tool_name in sorted(active_tools):
        if tool_name in _tool_docs:
            docs.append(_tool_docs[tool_name])

    if not docs:
        return ""

    header = """## TOOLS

When a user's request matches a tool below, you MUST use the TOOL tag. Do NOT answer the request yourself — use the tool.
You do NOT need to be addressed by name — when the user clearly wants an action a tool can do, just use the tool.

Format — TOOL tag BEFORE the emotion tag:
[TOOL:tool_name:argument]
[EMOTION] Your in-character response about the action.

IMPORTANT: If the user asks to add/show/complete tasks, log expenses, check weather, etc. — ALWAYS use the matching tool. Never handle these requests without the tool tag.

Available tools:
"""
    return header + "\n\n".join(docs)


def setup_default_routes() -> None:
    """Register the built-in keyword routes for existing tools."""

    # Costume tools
    register_route(
        r"\b(?:change|switch|swap|wear|put on|outfit|costume|dress|clothes|maid|casual|idol|favorite|comfy|elegant|fancy|cozy|chill)\b",
        ["change_costume"],
    )

    # Memory tools are always active (registered in always_active set),
    # but explicit triggers can boost priority
    register_route(
        r"\b(?:remember|forget|memory|memorize|don'?t forget|update memory|delete memory|note\s+this|keep\s+in\s+mind|you\s+should\s+know|FYI)\b",
        ["save_memory", "update_memory", "delete_memory"],
    )

    # Task tools moved to plugins/todo/ — loaded by plugin_loader

    # Weather, Calendar, Money, Meme, Calorie, Anime moved to plugins/ — loaded by plugin_loader

    # Lucky draw tools (gacha, roulette, THR) — main features, stay here
    register_route(
        r"\b(?:gacha|pull|draw|summon|banner)\b",
        ["open_gacha"],
    )
    register_route(
        r"\b(?:roulette|wheel|undian|spin|roll)\b",
        ["open_roulette", "spin_roulette"],
    )
    register_route(
        r"\b(?:thr|angpao|envelope|tunjangan|hari\s*raya)\b",
        ["give_thr"],
    )

    logger.info(f"[tool_router] {len(_routes)} built-in routes registered")


def register_plugin_routes() -> None:
    """Register routes and docs from loaded plugins."""
    from services.plugin_loader import get_plugin_triggers, get_plugin_prompt_docs

    for pattern, tool_names, requires_intent in get_plugin_triggers():
        register_route(pattern, tool_names, requires_intent=requires_intent)
        logger.info(f"[tool_router] Plugin route: {tool_names} (intent_required={requires_intent})")

    for tool_name, doc in get_plugin_prompt_docs().items():
        register_tool_doc(tool_name, doc)
        logger.info(f"[tool_router] Plugin doc: {tool_name} ({len(doc)} chars)")

    logger.info(f"[tool_router] Total routes after plugins: {len(_routes)}")
