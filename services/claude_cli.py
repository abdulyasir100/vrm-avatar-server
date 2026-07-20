"""Claude Code CLI integration — runs claude -p as subprocess.

Uses Claude Max subscription via the claude CLI binary.
No API keys needed — authentication is handled by the CLI.

Supports two modes:
- query_claude_cli(): returns full response string (smart/code modes)
- stream_claude_cli(): async generator yielding text deltas (fast mode, for streaming TTS)
"""

import asyncio
import json
import logging
import os
import re
import shutil
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# StreamReader buffer for claude CLI subprocesses. The default (64 KiB) is not
# enough when the CLI emits stream-json events containing Read-tool results for
# images — a single line carrying base64 PNG data easily exceeds that and
# triggers `LimitOverrunError: Separator is not found, and chunk exceed the
# limit`, killing the process and falling through to text-only fallbacks.
# 10 MiB comfortably handles photos up to ~7 MiB raw.
_STREAM_LIMIT = 10 * 1024 * 1024

# Patterns that mean claude CLI returned an upstream error disguised as a response.
# When matched, query_claude_cli returns None so the llm fallback chain (Groq/Cerebras) takes over.
_AUTH_ERROR_PATTERNS = (
    "Failed to authenticate",
    "authentication_error",
    "Invalid authentication credentials",
)

# Any `API Error: <4xx/5xx>` the CLI surfaces as response text is an upstream
# failure (auth 401/403, overload 429, server 5xx) — not a real reply. Match the
# whole class so a Claude API outage falls through to fallback instead of being
# spoken verbatim (e.g. the 2026-06-23 "API Error: 500..." leak into chat).
_API_ERROR_RE = re.compile(r"API Error:\s*(?:4|5)\d\d")

# Chat-template role markers that sometimes leak into completions because
# _build_prompt() flattens the conversation as "User: ...\nAssistant: ...".
# Anything after these on a new line is a hallucinated continuation — strip it.
_TEMPLATE_LEAK_RE = re.compile(r"\n\s*(?:User|Assistant|Human):\s", re.IGNORECASE)


def _sanitize_cli_response(text: str) -> str | None:
    """Post-process a claude CLI response. Returns None for auth/upstream errors."""
    if not text:
        return None
    stripped = text.strip()
    for marker in _AUTH_ERROR_PATTERNS:
        if marker in stripped:
            logger.warning(f"[claude_cli] Upstream error detected ({marker!r}), returning None to trigger fallback")
            return None
    api_err = _API_ERROR_RE.search(stripped)
    if api_err:
        logger.warning(f"[claude_cli] Upstream API error detected ({api_err.group()!r}), returning None to trigger fallback")
        return None
    # Truncate chat-template leak (hallucinated next turn)
    m = _TEMPLATE_LEAK_RE.search(stripped)
    if m:
        logger.info(f"[claude_cli] Stripped chat-template leak at offset {m.start()}")
        stripped = stripped[: m.start()].rstrip()
    return stripped or None

# Env vars passed to subprocess to skip non-essential work
_SUBPROCESS_ENV = {
    **os.environ,
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}


def _build_cmd(
    prompt: str,
    model: str,
    effort: str,
    system_prompt: str,
    allowed_tools: str,
    streaming: bool = False,
) -> list[str]:
    """Build the claude CLI command array."""
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--no-session-persistence",
    ]

    if streaming:
        cmd.extend(["--output-format", "stream-json", "--verbose", "--include-partial-messages"])
    else:
        cmd.extend(["--output-format", "stream-json", "--verbose"])

    if effort:
        cmd.extend(["--effort", effort])
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    if allowed_tools:
        cmd.extend(["--allowedTools", allowed_tools])

    return cmd


def _build_prompt(messages: list[dict]) -> tuple[str, str]:
    """Extract system prompt and conversation from messages list.

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


async def query_claude_cli(
    messages: list[dict],
    model: str = "haiku",
    effort: str = "low",
    timeout: int = 60,
    allowed_tools: str = "",
    cwd: str | None = None,
) -> str | None:
    """Run claude -p as subprocess. Returns response text or None.

    Uses stream-json format and reads the final result event.
    Used by smart/code modes where streaming TTS doesn't apply.
    """
    prompt, system_prompt = _build_prompt(messages)
    if not prompt:
        logger.warning("[claude_cli] No prompt to send")
        return None

    cmd = _build_cmd(prompt, model, effort, system_prompt, allowed_tools, streaming=False)

    logger.info(f"[claude_cli] Running: model={model} effort={effort} timeout={timeout}s tools={allowed_tools or 'none'}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=_SUBPROCESS_ENV,
            limit=_STREAM_LIMIT,
        )

        result_text = ""

        async def _read_stream():
            nonlocal result_text
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    etype = event.get("type")
                    if etype == "result":
                        result_text = event.get("result", "")
                except json.JSONDecodeError:
                    continue

        await asyncio.wait_for(_read_stream(), timeout=timeout)
        await proc.wait()

        if result_text:
            logger.info(f"[claude_cli] Response ({len(result_text)} chars): {result_text[:100]}...")
            sanitized = _sanitize_cli_response(result_text)
            if sanitized is None:
                # Auth error or empty — fall through to llm fallback chain
                return None
            return sanitized

        # Fallback: check if process had errors
        if proc.returncode != 0:
            stderr = await proc.stderr.read()
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"[claude_cli] Exit code {proc.returncode}: {err[:200]}")

        logger.warning("[claude_cli] No result text from stream")
        return None

    except asyncio.TimeoutError:
        logger.warning(f"[claude_cli] Timeout after {timeout}s, killing process")
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None
    except FileNotFoundError as e:
        logger.warning(f"[claude_cli] FileNotFoundError: {e} (check binary PATH and cwd={cwd})")
        return None
    except Exception as e:
        logger.error(f"[claude_cli] Unexpected error: {e}")
        return None


# Sentence boundary regex: split on .!? followed by space (same logic as tts_service.split_sentences)
_SENTENCE_END = re.compile(r'(?<=[.!?。！？])\s')


async def stream_claude_cli(
    messages: list[dict],
    model: str = "haiku",
    effort: str = "low",
    timeout: int = 60,
    allowed_tools: str = "",
    cwd: str | None = None,
    min_chunk_chars: int = 40,
) -> AsyncGenerator[dict, None]:
    """Stream claude -p response, yielding sentences as they complete.

    Yields dicts:
      {"type": "sentence", "text": "..."}     — a complete sentence ready for TTS
      {"type": "done", "full_text": "..."}     — final complete text

    Text deltas are accumulated and split at sentence boundaries.
    The first yield happens as soon as a complete sentence (>= min_chunk_chars) is detected.
    """
    prompt, system_prompt = _build_prompt(messages)
    if not prompt:
        logger.warning("[claude_cli] No prompt to send")
        yield {"type": "done", "full_text": ""}
        return

    cmd = _build_cmd(prompt, model, effort, system_prompt, allowed_tools, streaming=True)

    logger.info(f"[claude_cli:stream] Running: model={model} effort={effort} timeout={timeout}s tools={allowed_tools or 'none'}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=_SUBPROCESS_ENV,
            limit=_STREAM_LIMIT,
        )

        accumulated = ""  # Full text accumulated from deltas
        yielded_up_to = 0  # How much of accumulated has been yielded as sentences
        result_text = ""  # Final result from result event
        in_text_block = False  # True when we're inside a text content block

        try:
            while True:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=timeout
                )
                if not line:
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")

                if etype == "stream_event":
                    se = event.get("event", {})
                    se_type = se.get("type")

                    if se_type == "content_block_start":
                        block = se.get("content_block", {})
                        if block.get("type") == "text":
                            in_text_block = True

                    elif se_type == "content_block_stop":
                        in_text_block = False

                    elif se_type == "content_block_delta" and in_text_block:
                        delta = se.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            accumulated += text

                            # Check for complete sentences in the un-yielded portion
                            remaining = accumulated[yielded_up_to:]
                            parts = _SENTENCE_END.split(remaining)

                            # If we have more than one part, all but last are complete sentences
                            if len(parts) > 1:
                                for sentence in parts[:-1]:
                                    sentence = sentence.strip()
                                    if len(sentence) >= min_chunk_chars:
                                        yield {"type": "sentence", "text": sentence}
                                        yielded_up_to += len(sentence) + 1  # +1 for the split space
                                    elif sentence:
                                        # Too short — will merge with next
                                        pass

                elif etype == "result":
                    result_text = event.get("result", "")

        except asyncio.TimeoutError:
            logger.warning(f"[claude_cli:stream] Timeout after {timeout}s")
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

        await proc.wait()

        # Use result_text (authoritative) if available, otherwise accumulated
        final_text = result_text or accumulated

        # Auth error detection: if upstream returned an error, signal empty done so caller falls back
        for marker in _AUTH_ERROR_PATTERNS:
            if marker in (final_text or ""):
                logger.warning(f"[claude_cli:stream] Upstream error detected ({marker!r}), yielding empty done")
                yield {"type": "done", "full_text": ""}
                return

        # Truncate chat-template leak on final_text
        m = _TEMPLATE_LEAK_RE.search(final_text or "")
        if m:
            logger.info(f"[claude_cli:stream] Stripped chat-template leak at offset {m.start()}")
            final_text = final_text[: m.start()].rstrip()

        # Yield any remaining un-yielded text as final sentence
        if yielded_up_to < len(accumulated):
            remaining = accumulated[yielded_up_to:].strip()
            # Also strip template leak from trailing chunk
            m2 = _TEMPLATE_LEAK_RE.search(remaining)
            if m2:
                remaining = remaining[: m2.start()].rstrip()
            if remaining:
                yield {"type": "sentence", "text": remaining}

        yield {"type": "done", "full_text": final_text}

    except FileNotFoundError as e:
        logger.warning(f"[claude_cli:stream] FileNotFoundError: {e}")
        yield {"type": "done", "full_text": ""}
    except Exception as e:
        logger.error(f"[claude_cli:stream] Unexpected error: {e}")
        yield {"type": "done", "full_text": ""}


async def check_claude_cli() -> bool:
    """Check if claude binary is available."""
    if shutil.which("claude"):
        return True
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0
    except Exception:
        return False
