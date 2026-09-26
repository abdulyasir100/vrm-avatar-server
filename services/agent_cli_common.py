"""Shared helpers for agentic-CLI backends (claude_cli, opencode_cli).

Both backends shell out to a coding-agent binary, flatten the message list into
a single prompt, and post-process the reply before it reaches TTS. That logic is
identical between them, so it lives here — one source of truth.

Backend-specific pieces (command construction, stream event schema, subprocess
env) stay in the individual modules.
"""

import json
import logging
import re
import tempfile
from pathlib import Path

import config

logger = logging.getLogger(__name__)
perf = logging.getLogger("avatar.perf")

# Spawn claude processes (CLI + SDK) in an EMPTY directory instead of the app root, so the
# CLI doesn't walk the repo for CLAUDE.md / .claude / .mcp.json on every boot. Benchmarked in
# town.exe: ~30% lower median call latency and no 13-17s tail spikes. Code mode keeps its
# sandbox cwd (it needs the files there).
NEUTRAL_CWD = tempfile.mkdtemp(prefix="avatar_claude_cwd_")


def fallback_for(model: str) -> str | None:
    """Overload fallback model for `model`, or None when disabled or identical."""
    fb = (config.CLAUDE_FALLBACK_MODEL or "").strip()
    return fb if fb and fb != model else None


def tool_names(allowed_tools: str) -> list[str]:
    """Built-in tool names from an --allowedTools list: 'Bash(git *),Read' -> ['Bash','Read']."""
    names = []
    for part in (allowed_tools or "").split(","):
        name = part.split("(", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def record_perf(kind: str, model: str, **fields) -> None:
    """One structured timing line per LLM call on the `avatar.perf` logger.

    Fields worth reading together: queued (waiting on the concurrency gate = saturation),
    warm (skipped CLI boot), connect/ran (boot vs generation).
    """
    parts = []
    for key, val in fields.items():
        if isinstance(val, bool):
            val = int(val)
        elif isinstance(val, float):
            val = f"{val:.2f}s"
        parts.append(f"{key}={val}")
    perf.info(f"[perf] call kind={kind} model={model} " + " ".join(parts))


def capture(model: str, system_prompt: str, prompt: str, response: str | None) -> None:
    """Append one call's full payload to logs/llm_calls.jsonl when LLM_CAPTURE is on."""
    if not config.LLM_CAPTURE:
        return
    try:
        path = Path(config.LOG_DIR)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / "llm_calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "model": model, "system_prompt": system_prompt or "",
                "user_prompt": prompt, "response": response or "",
            }, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("[agent_cli] capture failed")

# StreamReader buffer for agent CLI subprocesses. The default (64 KiB) is not
# enough when a CLI emits stream events containing Read-tool results for images —
# a single line carrying base64 PNG data easily exceeds that and triggers
# `LimitOverrunError: Separator is not found, and chunk exceed the limit`,
# killing the process and falling through to text-only fallbacks.
# 10 MiB comfortably handles photos up to ~7 MiB raw.
STREAM_LIMIT = 10 * 1024 * 1024

# Patterns that mean the CLI returned an upstream error disguised as a response.
# When matched, the caller returns None so the llm fallback chain takes over.
_AUTH_ERROR_PATTERNS = (
    "Failed to authenticate",
    "authentication_error",
    "Invalid authentication credentials",
)

# Any `API Error: <4xx/5xx>` a CLI surfaces as response text is an upstream
# failure (auth 401/403, overload 429, server 5xx) — not a real reply. Match the
# whole class so a provider outage falls through to fallback instead of being
# spoken verbatim (e.g. the 2026-06-23 "API Error: 500..." leak into chat).
_API_ERROR_RE = re.compile(r"API Error:\s*(?:4|5)\d\d")

# Chat-template role markers that sometimes leak into completions because
# build_prompt() flattens the conversation as "User: ...\nAssistant: ...".
# Anything after these on a new line is a hallucinated continuation — strip it.
_TEMPLATE_LEAK_RE = re.compile(r"\n\s*(?:User|Assistant|Human):\s", re.IGNORECASE)


def sanitize_cli_response(text: str, tag: str = "agent_cli") -> str | None:
    """Post-process an agent CLI response. Returns None for auth/upstream errors."""
    if not text:
        return None
    stripped = text.strip()
    for marker in _AUTH_ERROR_PATTERNS:
        if marker in stripped:
            logger.warning(f"[{tag}] Upstream error detected ({marker!r}), returning None to trigger fallback")
            return None
    api_err = _API_ERROR_RE.search(stripped)
    if api_err:
        logger.warning(f"[{tag}] Upstream API error detected ({api_err.group()!r}), returning None to trigger fallback")
        return None
    # Truncate chat-template leak (hallucinated next turn)
    m = _TEMPLATE_LEAK_RE.search(stripped)
    if m:
        logger.info(f"[{tag}] Stripped chat-template leak at offset {m.start()}")
        stripped = stripped[: m.start()].rstrip()
    return stripped or None


def build_prompt(messages: list[dict]) -> tuple[str, str]:
    """Extract system prompt and conversation from messages list.

    Returns (conversation, system_prompt).

    Accumulate ALL system messages — do not let a later one overwrite earlier.
    A reply-to note ("[User is replying to this message: ...]") is injected as a
    second system message after history; the old "last wins" behavior clobbered the
    big personality/memory/tools system prompt with just that note, so on the Claude
    path any Telegram swipe-reply stripped her character/context.
    """
    system_parts = []
    conversation_parts = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(msg["content"])
        elif msg["role"] == "user":
            conversation_parts.append(f"User: {msg['content']}")
        elif msg["role"] == "assistant":
            conversation_parts.append(f"Assistant: {msg['content']}")
    prompt = "\n".join(conversation_parts) if conversation_parts else ""
    system_prompt = "\n\n".join(system_parts)
    return prompt, system_prompt
