"""OpenCode CLI integration — runs `opencode run` as subprocess.

Second agentic-CLI backend alongside claude_cli. Used as a fallback when the
Claude CLI fails (rate limit, auth, outage) so the companion keeps its full
personality instead of dropping straight to the terser Groq path.

Auth is handled by the CLI itself (`opencode auth login`) — no API key here.
Which provider that login points at (xAI, Anthropic, OpenCode Zen) is the
operator's choice; this module only names a model.

Two differences from the Claude CLI shape the implementation:

1. There is no `--system-prompt` flag, so the system block is prepended to the
   prompt text instead of passed separately.
2. There is no `--allowedTools` flag. Tool permissions live in an
   `opencode.json` agent definition selected with `--agent`. With no agent
   configured, OpenCode runs with its own defaults — fine for conversational
   replies, which is all the fallback path needs.

Event schema (`--format json`), verified on the Ubuntu host against opencode
1.2.6 and again on 1.18.23 — unchanged across both, so the shape is stable:
    {"type":"step_start",  "part":{...}}
    {"type":"text",        "part":{"id":"prt_...","type":"text","text":"PONG"}}
    {"type":"step_finish", "part":{"reason":"stop","tokens":{...}}}
    {"type":"error",       "error":{"name":"UnknownError","data":{"message":"..."}}}
"""

import asyncio
import json
import logging
import os
import shutil

import config
from services.agent_cli_common import STREAM_LIMIT, build_prompt, sanitize_cli_response

logger = logging.getLogger(__name__)

# Skip the update check on every invocation — this runs on a request path.
_SUBPROCESS_ENV = {
    **os.environ,
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
}


def is_available() -> bool:
    """True when the opencode binary is on PATH."""
    return shutil.which(config.OPENCODE_BIN) is not None


def _build_cmd(
    prompt: str,
    model: str,
    variant: str,
    agent: str,
    cwd: str | None,
) -> list[str]:
    """Build the opencode CLI command array."""
    cmd = [config.OPENCODE_BIN, "run", prompt, "--format", "json"]

    if model:
        cmd.extend(["-m", model])
    if variant:
        cmd.extend(["--variant", variant])
    if agent:
        cmd.extend(["--agent", agent])
    # --dir rather than the subprocess cwd: opencode resolves its project
    # context from this flag, and passing it explicitly keeps the sandbox
    # boundary visible in the logged command.
    if cwd:
        cmd.extend(["--dir", cwd])

    return cmd


def _extract_text(events: list[dict]) -> str:
    """Join the assistant's text parts, in emission order.

    Keyed by part id so that a backend streaming incremental updates for the
    same part (later event carrying the fuller string) replaces rather than
    duplicates it. Dicts preserve insertion order, so distinct parts stay
    in sequence.
    """
    parts: dict[str, str] = {}
    for event in events:
        if event.get("type") != "text":
            continue
        part = event.get("part") or {}
        text = part.get("text")
        if not text:
            continue
        parts[part.get("id") or str(len(parts))] = text
    return "".join(parts.values()).strip()


async def query_opencode_cli(
    messages: list[dict],
    model: str = "",
    variant: str = "",
    timeout: int = 120,
    agent: str = "",
    cwd: str | None = None,
) -> str | None:
    """Run `opencode run` as subprocess. Returns response text, or None on failure.

    None is the signal to continue down the llm fallback chain — every failure
    mode (missing binary, auth error, timeout, empty result) returns it rather
    than raising, so a broken OpenCode install can never take the server down.
    """
    conversation, system_prompt = build_prompt(messages)
    if not conversation:
        logger.warning("[opencode_cli] No prompt to send")
        return None

    if not is_available():
        logger.warning(
            f"[opencode_cli] Binary {config.OPENCODE_BIN!r} not found on PATH — "
            "install it in the image, or unset AGENT_CLI_FALLBACK"
        )
        return None

    # No --system-prompt flag on opencode; fold it into the prompt body.
    prompt = f"{system_prompt}\n\n---\n\n{conversation}" if system_prompt else conversation

    model = model or config.OPENCODE_MODEL
    cmd = _build_cmd(prompt, model, variant, agent, cwd)

    logger.info(
        f"[opencode_cli] Running: model={model} variant={variant or 'default'} "
        f"timeout={timeout}s agent={agent or 'default'} dir={cwd or 'cwd'}"
    )

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_SUBPROCESS_ENV,
            limit=STREAM_LIMIT,
        )

        events: list[dict] = []
        error_message = ""

        async def _read_stream():
            nonlocal error_message
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON chrome on stdout — ignore rather than abort.
                    continue
                if event.get("type") == "error":
                    err = event.get("error") or {}
                    data = err.get("data") or {}
                    error_message = data.get("message") or err.get("name") or "unknown error"
                    continue
                events.append(event)

        await asyncio.wait_for(_read_stream(), timeout=timeout)
        await proc.wait()

        if error_message:
            logger.warning(f"[opencode_cli] CLI reported error: {error_message[:200]}")
            return None

        result_text = _extract_text(events)
        if result_text:
            logger.info(f"[opencode_cli] Response ({len(result_text)} chars): {result_text[:100]}...")
            return sanitize_cli_response(result_text, tag="opencode_cli")

        if proc.returncode != 0:
            stderr = await proc.stderr.read()
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"[opencode_cli] Exit code {proc.returncode}: {err[:200]}")

        logger.warning("[opencode_cli] No text parts in stream")
        return None

    except asyncio.TimeoutError:
        logger.warning(f"[opencode_cli] Timeout after {timeout}s, killing process")
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None
    except FileNotFoundError as e:
        logger.warning(f"[opencode_cli] FileNotFoundError: {e}")
        return None
    except Exception as e:
        logger.error(f"[opencode_cli] Unexpected error: {e}")
        return None
