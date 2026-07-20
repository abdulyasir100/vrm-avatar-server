"""Claude Agent SDK integration — native tool calling via in-process MCP server.

Replaces the text-tag `[TOOL:name:arg]` emulation on the Claude path with native
function calling. Every registered tool (main features + plugins) is exposed as a
single in-process MCP server named "avatar"; the model calls them itself in an
agentic loop — no intent prefix, no keyword pre-filter, no tag parsing, no ~5%
miss-rate fallback hacks. This is what makes the avatar feel seamless, like
talking to Claude Code.

Gated by config.CLAUDE_USE_SDK. When off, llm.py uses the legacy claude_cli path,
which stays intact as instant rollback alongside the `pre-seamless-tools` git tag.

Performance design — persistent warm client:
- A single ClaudeSDKClient is kept connected across turns (the MCP server with all
  tools is built once). This avoids spawning the bundled CLI + re-doing the MCP
  handshake on every request — cold ~12-16s vs warm ~5s.
- Correctness is preserved by two guarantees:
    1. An asyncio.Lock serializes turns, so only ONE turn is ever in flight. That
       lets the tool handlers read per-turn state (the side-effect sink + request
       context) from a module-global container without any cross-request races —
       there is never a second concurrent turn to contaminate it. (A contextvar
       does NOT work here: the in-process MCP handler runs in a different async
       task, so the var wouldn't propagate.)
    2. Each turn uses a fresh session_id, so the persistent process never carries
       conversation history between turns. avatar-server's own reconstructed prompt
       (system + memory history + message) remains the single source of truth — no
       doubled/leaked context. The client is also recycled every _RECONNECT_EVERY
       turns to drop accumulated per-session state in the long-running process.
- Because the persistent client's system prompt is fixed at construction, the
  per-turn dynamic context (personality, memories, mood, time, history) rides in
  the prompt instead of options.system_prompt; only the static identity note is
  the client's system prompt.

run_agent() returns the same result-dict shape as the legacy path PLUS
`sdk_tools_ran` (list of {tool, arg, result}) so routers/chat.py can apply
side-effects / skip flags WITHOUT re-executing the handler (the SDK already ran it).
On any SDK error it returns None, so llm.py falls through to the Groq/Cerebras chain.
"""

import asyncio
import json
import logging
from typing import Any

import config
from services import tool_registry

logger = logging.getLogger(__name__)

# The persistent client's fixed system prompt. Personality/memory/mood/etc. ride in
# the per-turn prompt (see module docstring), so this asserts identity + tool ownership
# and tells the model to fully honor the in-character framing carried by the prompt.
_IDENTITY_NOTE = (
    "You are the user's companion character described in the prompt below — stay "
    "fully in that character, voice, and mood at all times; the prompt is your "
    "real personality, not a roleplay request.\n\n"
    "You OWN the action tools available to you — they directly modify the user's "
    "own data and devices. When the user clearly wants something a tool does "
    "(logging spending or meals, managing tasks, checking weather/balance, changing "
    "your costume, etc.), CALL the tool yourself, immediately. You ARE this system — "
    "never tell the user to message a bot or do it elsewhere, and you do not need to "
    "be addressed by name to act. After a tool runs, reply in one short in-character "
    "line; the tool result is already reflected, so don't dump raw data unless it's a "
    "figure worth saying out loud (balance, calories left)."
)

_MCP_SERVER_NAME = "avatar"
_RECONNECT_EVERY = 50  # recycle the warm client every N turns to drop session buildup

# Module-global per-turn state. Safe because run_agent holds _lock for the whole turn,
# so only one turn (and its tool calls) is ever live. Tool handlers read these.
_G: dict[str, Any] = {"sink": None, "ctx": None}

_lock = asyncio.Lock()
_client = None           # persistent ClaudeSDKClient
_server = None           # built-once in-process MCP server
_allowed: list[str] = []  # mcp__avatar__* tool names exposed
_turn_seq = 0            # unique session_id source
_turns_since_reconnect = 0


def is_available() -> bool:
    """True if the SDK is importable. Lets llm.py fall back gracefully if not installed."""
    try:
        import claude_agent_sdk  # noqa: F401
        return True
    except Exception as e:  # pragma: no cover
        logger.warning(f"[claude_agent] SDK not importable: {e}")
        return False


def _resolve_sdk_tools() -> list[str]:
    """Which tool names to expose. No keyword pre-filter — the model sees all tools
    (the seamless win); only TOOL_CALL_MODE coarse gates apply. Resolved once when
    the persistent MCP server is built."""
    mode = config.TOOL_CALL_MODE
    if mode == "off":
        return []
    names = set(tool_registry.list_tools())
    if mode == "semi_normal":
        names &= tool_registry.MAIN_TOOLS
    return sorted(names)


def _make_sdk_tool(name: str, schema: dict):
    """Wrap one registered tool as an SDK in-process MCP tool. Reads per-turn sink +
    context from the module-global _G (valid because run_agent holds _lock)."""
    from claude_agent_sdk import tool as sdk_tool

    description = schema.get("description", name)
    parameters = schema.get("parameters", {"arg": str})

    @sdk_tool(name, description, parameters)
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        handler = tool_registry.get(name)
        if not handler:
            return {"content": [{"type": "text", "text": f"Tool {name} is unavailable."}], "is_error": True}

        arg = args.get("arg")
        if arg is None:
            arg = json.dumps(args) if args else ""

        try:
            res = await handler(arg or "", _G["ctx"] or {})
        except Exception as e:
            logger.error(f"[claude_agent] tool '{name}' raised: {e}")
            return {"content": [{"type": "text", "text": f"That didn't work: {e}"}], "is_error": True}

        if _G["sink"] is not None:
            _G["sink"].append({"tool": name, "arg": arg or "", "result": res})

        text = (res or {}).get("result") or "(done)"
        return {"content": [{"type": "text", "text": text}], "is_error": not (res or {}).get("ok", True)}

    return _handler


def _build_server():
    """Build the in-process MCP server once from the current registry."""
    from claude_agent_sdk import create_sdk_mcp_server
    global _server, _allowed
    names = _resolve_sdk_tools()
    tools = [_make_sdk_tool(n, tool_registry._schemas.get(n, {"description": n})) for n in names]
    _server = create_sdk_mcp_server(name=_MCP_SERVER_NAME, version="1.0.0", tools=tools)
    _allowed = [f"mcp__{_MCP_SERVER_NAME}__{n}" for n in names]
    logger.info(f"[claude_agent] built MCP server with {len(names)} tools")


async def _ensure_client(model: str):
    """Return a connected persistent client, (re)creating it as needed. None on failure."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    global _client, _turns_since_reconnect

    # Periodic recycle to drop accumulated per-session state in the long-running CLI.
    if _client is not None and _turns_since_reconnect >= _RECONNECT_EVERY:
        await _shutdown_client()

    if _client is not None:
        return _client

    if _server is None:
        _build_server()

    try:
        options = ClaudeAgentOptions(
            system_prompt=_IDENTITY_NOTE,
            model=model,
            max_turns=6,
            mcp_servers={_MCP_SERVER_NAME: _server} if _allowed else {},
            allowed_tools=_allowed,
        )
        client = ClaudeSDKClient(options=options)
        await client.connect()
        _client = client
        _turns_since_reconnect = 0
        logger.info("[claude_agent] persistent client connected")
        return _client
    except Exception as e:
        logger.error(f"[claude_agent] client connect failed: {e}")
        _client = None
        return None


async def _shutdown_client():
    """Disconnect and clear the persistent client (best-effort)."""
    global _client
    if _client is not None:
        try:
            await _client.disconnect()
        except Exception:
            pass
        _client = None


async def run_agent(
    message: str,
    context: str,
    user_name: str,
    reply_to: str | None = None,
    is_system_prompt: bool = False,
    model: str | None = None,
) -> dict | None:
    """Run one companion turn through the persistent Claude Agent SDK client.

    Returns a result dict compatible with the legacy path
    ({"reply","emotion","tool_name","tool_arg","sdk_tools_ran"}) or None on failure.
    """
    try:
        from claude_agent_sdk import ResultMessage  # noqa: F401
    except Exception as e:
        logger.warning(f"[claude_agent] SDK import failed, falling back: {e}")
        return None

    # Lazy import to avoid a circular import (llm imports this module to route).
    from services import llm
    from services.claude_cli import _build_prompt

    # Reuse the exact personality + memory + mood + context assembly. use_function_calling=True
    # skips the text-tag TOOLS block (tools are passed natively instead). The full assembled
    # prompt (system content + history + message) rides as the per-turn prompt; the persistent
    # client's own system prompt is just the identity note.
    messages = llm._build_messages(
        message, context, user_name, use_function_calling=True,
        reply_to=reply_to, is_system_prompt=is_system_prompt,
    )
    sys_block, conversation = _build_prompt(messages)
    if not conversation and not sys_block:
        return None
    full_prompt = f"{sys_block}\n\n{conversation}".strip() if sys_block else conversation

    from claude_agent_sdk import ResultMessage
    global _turn_seq, _turns_since_reconnect

    async with _lock:
        _G["sink"] = []
        _G["ctx"] = {"context": context, "user_name": user_name}
        try:
            client = await _ensure_client(model or config.get_llm_model())
            if client is None:
                return None

            _turn_seq += 1
            session_id = f"turn-{_turn_seq}"  # fresh per turn → no history leakage

            async def _collect() -> str:
                final = ""
                await client.query(full_prompt, session_id=session_id)
                async for msg in client.receive_response():
                    if isinstance(msg, ResultMessage):
                        final = msg.result or final
                return final

            try:
                # Timeout so a hung CLI can't hold the turn lock forever — drop the
                # client and fall through to the fallback chain.
                final_text = await asyncio.wait_for(_collect(), timeout=config.CLAUDE_SDK_TIMEOUT)
            except (Exception, asyncio.TimeoutError) as e:
                logger.error(f"[claude_agent] query failed/timeout, dropping client: {e}")
                await _shutdown_client()
                return None

            _turns_since_reconnect += 1
            sink = list(_G["sink"])
        finally:
            _G["sink"] = None
            _G["ctx"] = None

    if not final_text and not sink:
        return None

    emotion, clean_reply = llm.parse_emotion(final_text)
    primary = sink[0] if sink else None
    return {
        "reply": clean_reply,
        "emotion": emotion,
        "tool_name": primary["tool"] if primary else None,
        "tool_arg": primary["arg"] if primary else None,
        "sdk_tools_ran": sink,
    }
