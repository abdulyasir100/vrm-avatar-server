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
On any SDK error it returns None, so llm.py falls through to the Groq fallback chain.
"""

import asyncio
import json
import logging
import time
from typing import Any

import config
from services import access
from services import agent_cli_common as common
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
_client_key: tuple | None = None  # options the live client was built with


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
    if mode == "off" or not access.allows("tools"):
        return []
    names = set(tool_registry.list_tools())
    if mode == "semi_normal":
        names &= tool_registry.MAIN_TOOLS
    if not config.SHELL_ENABLED or not access.allows("shell"):
        names.discard("run_shell")
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


def _desired_key(model: str) -> tuple:
    """Everything baked into the client/MCP server at connect. A change means reconnect."""
    return (model, config.CLAUDE_SDK_EFFORT, common.fallback_for(model),
            config.CLAUDE_SDK_ISOLATE, config.TOOL_CALL_MODE, config.SHELL_ENABLED, config.ACCESS_LEVEL)


async def _ensure_client(model: str):
    """Return a connected persistent client, (re)creating it as needed. None on failure.

    Called under _lock, so a reconnect never kills an in-flight turn.
    """
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    global _client, _server, _client_key, _turns_since_reconnect

    key = _desired_key(model)
    if _client is not None and _client_key != key:
        logger.info(f"[claude_agent] options changed {_client_key} -> {key}, reconnecting")
        await _shutdown_client()
        _server = None  # tool set may have changed (TOOL_CALL_MODE / SHELL_ENABLED)

    # Periodic recycle to drop accumulated per-session state in the long-running CLI.
    if _client is not None and _turns_since_reconnect >= _RECONNECT_EVERY:
        await _shutdown_client()

    if _client is not None:
        return _client

    if _server is None:
        _build_server()

    try:
        # Lean boot (speed pack): neutral empty cwd, no built-in tools (their schemas rode in
        # every turn; the avatar MCP tools are unaffected), only our MCP server.
        opts = dict(
            system_prompt=_IDENTITY_NOTE,
            model=model,
            max_turns=6,
            mcp_servers={_MCP_SERVER_NAME: _server} if _allowed else {},
            allowed_tools=_allowed,
            cwd=common.NEUTRAL_CWD,
            tools=[],
            strict_mcp_config=True,
        )
        fallback = common.fallback_for(model)
        if fallback:
            opts["fallback_model"] = fallback
        if config.CLAUDE_SDK_EFFORT:
            opts["effort"] = config.CLAUDE_SDK_EFFORT
        if config.CLAUDE_SDK_ISOLATE:
            opts["setting_sources"] = []
        client = ClaudeSDKClient(options=ClaudeAgentOptions(**opts))
        await client.connect()
        _client = client
        _client_key = key
        _turns_since_reconnect = 0
        logger.info(f"[claude_agent] persistent client connected key={key}")
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


async def shutdown():
    """Disconnect the persistent client (app shutdown)."""
    async with _lock:
        await _shutdown_client()


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
    # build_prompt returns (conversation, system_prompt) — system content goes FIRST.
    conversation, sys_block = _build_prompt(messages)
    if not conversation and not sys_block:
        return None
    full_prompt = f"{sys_block}\n\n{conversation}".strip() if sys_block else conversation

    from claude_agent_sdk import ResultMessage
    global _turn_seq, _turns_since_reconnect

    async with _lock:
        _G["sink"] = []
        _G["ctx"] = {"context": context, "user_name": user_name}
        try:
            model = model or config.get_llm_model()
            t0 = time.perf_counter()
            client = await _ensure_client(model)
            connect_s = time.perf_counter() - t0
            if client is None:
                return None

            _turn_seq += 1
            session_id = f"turn-{_turn_seq}"  # fresh per turn → no history leakage

            result_msg = None

            async def _collect() -> str:
                nonlocal result_msg
                final = ""
                await client.query(full_prompt, session_id=session_id)
                async for msg in client.receive_response():
                    if isinstance(msg, ResultMessage):
                        result_msg = msg
                        final = msg.result or final
                return final

            t1 = time.perf_counter()
            try:
                # Timeout so a hung CLI can't hold the turn lock forever — drop the
                # client and fall through to the fallback chain.
                final_text = await asyncio.wait_for(_collect(), timeout=config.CLAUDE_SDK_TIMEOUT)
            except (Exception, asyncio.TimeoutError) as e:
                logger.error(f"[claude_agent] query failed/timeout, dropping client: {e}")
                await _shutdown_client()
                common.record_perf("sdk", model, connect=connect_s,
                                   ran=time.perf_counter() - t1, ok=False)
                return None

            subtype = getattr(result_msg, "subtype", None)
            if subtype and subtype != "success":
                logger.warning(f"[claude_agent] turn ended with subtype={subtype}")
            common.record_perf(
                "sdk", model, effort=config.CLAUDE_SDK_EFFORT or "-",
                warm=connect_s < 0.05, connect=connect_s, ran=time.perf_counter() - t1,
                api_ms=getattr(result_msg, "duration_api_ms", None),
                turns=getattr(result_msg, "num_turns", None),
                tools=len(_G["sink"]), prompt_chars=len(full_prompt),
                out_chars=len(final_text or ""), subtype=subtype, ok=bool(final_text),
            )
            common.capture(model, _IDENTITY_NOTE, full_prompt, final_text)

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
