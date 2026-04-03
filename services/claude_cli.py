"""Claude Code CLI integration — runs claude -p as subprocess.

Uses Claude Max subscription via the claude CLI binary.
No API keys needed — authentication is handled by the CLI.
"""

import asyncio
import json
import logging
import shutil

logger = logging.getLogger(__name__)


async def query_claude_cli(
    messages: list[dict],
    model: str = "haiku",
    effort: str = "low",
    timeout: int = 60,
    allowed_tools: str = "",
    cwd: str | None = None,
) -> str | None:
    """Run claude -p as subprocess. Returns response text or None."""
    # Extract system prompt from messages
    system_prompt = ""
    conversation_parts = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        elif msg["role"] == "user":
            conversation_parts.append(f"User: {msg['content']}")
        elif msg["role"] == "assistant":
            conversation_parts.append(f"Assistant: {msg['content']}")

    prompt = "\n".join(conversation_parts) if conversation_parts else ""
    if not prompt:
        logger.warning("[claude_cli] No prompt to send")
        return None

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
    ]

    if effort:
        cmd.extend(["--effort", effort])
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    if allowed_tools:
        cmd.extend(["--allowedTools", allowed_tools])

    logger.info(f"[claude_cli] Running: model={model} effort={effort} timeout={timeout}s tools={allowed_tools or 'none'}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(f"[claude_cli] Exit code {proc.returncode}: {err[:200]}")
            return None

        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            logger.warning("[claude_cli] Empty stdout")
            return None

        try:
            data = json.loads(raw)
            result = data.get("result", "")
            if result:
                logger.info(f"[claude_cli] Response ({len(result)} chars): {result[:100]}...")
                return result
            logger.warning(f"[claude_cli] No 'result' in JSON: {list(data.keys())}")
            return None
        except json.JSONDecodeError:
            # Sometimes CLI outputs plain text
            logger.info(f"[claude_cli] Non-JSON response ({len(raw)} chars), using raw")
            return raw

    except asyncio.TimeoutError:
        logger.warning(f"[claude_cli] Timeout after {timeout}s, killing process")
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None
    except FileNotFoundError:
        logger.warning("[claude_cli] 'claude' binary not found in PATH")
        return None
    except Exception as e:
        logger.error(f"[claude_cli] Unexpected error: {e}")
        return None


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
