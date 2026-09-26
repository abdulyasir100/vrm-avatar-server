"""Claude Code CLI integration — runs `claude -p` as a subprocess.

Uses the Claude Max subscription via the claude CLI binary (no API key; the CLI
handles auth). query_claude_cli() returns the full response string and serves
smart / code / image / smart-idle / user_state calls and the provider fallback.
The production fast path is the Agent SDK (services/claude_agent.py).

Speed pack (ported from town.exe backend/ai/claude_client.py):
- neutral empty cwd + --strict-mcp-config + --tools: skip per-boot discovery work
  (code mode keeps its sandbox cwd and project config, and only gets --tools)
- --fallback-model: survive overloads without dropping to the plain-API chain
- concurrency gate + kill/reap inside the timeout budget: a wedged CLI can't pile up
- optional warm pool (CLAUDE_WARM_POOL, default 0): pre-booted one-shot processes
- one `avatar.perf` line per call, optional LLM_CAPTURE jsonl
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time

import config
from services import agent_cli_common as common
from services import floors

logger = logging.getLogger(__name__)

# Prompt flattening and response sanitizing are shared with opencode_cli —
# see services/agent_cli_common.py. Re-exported here under the original private
# names so existing importers (claude_agent.py) keep working.
from services.agent_cli_common import STREAM_LIMIT as _STREAM_LIMIT
from services.agent_cli_common import build_prompt as _build_prompt
from services.agent_cli_common import sanitize_cli_response as _sanitize_common


def _sanitize_cli_response(text: str) -> str | None:
    """Post-process a claude CLI response. Returns None for auth/upstream errors."""
    return _sanitize_common(text, tag="claude_cli")

# Env vars passed to subprocess to skip non-essential work
_SUBPROCESS_ENV = {
    **os.environ,
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

# Code mode has Bash, so it must not inherit the server's secrets (admin key, bot
# token, provider API keys). The CLI authenticates from ~/.claude, not from these.
_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|APPRISE_URLS", re.IGNORECASE)
_CODE_ENV = {k: v for k, v in _SUBPROCESS_ENV.items()
             if not _SECRET_ENV.search(k) or k.startswith(("ANTHROPIC_", "CLAUDE_"))}


def _boot_flags(model: str, allowed_tools: str, lean: bool = True) -> list[str]:
    """Per-spawn flags shared by the cold and warm paths.

    `--tools` restricts which built-in tools EXIST (""= none); `--allowedTools`
    auto-approves them. lean=False (code mode) skips --strict-mcp-config so the
    sandbox's own MCP config still loads, and puts the hard floor (services/floors.py)
    in front of every tool call as a PreToolUse hook.
    """
    flags = ["--strict-mcp-config"] if lean else ["--settings", floors.hook_settings()]
    flags += ["--tools", ",".join(common.tool_names(allowed_tools))]
    if allowed_tools:
        flags += ["--allowedTools", allowed_tools]
    fallback = common.fallback_for(model)
    if fallback:
        flags += ["--fallback-model", fallback]
    return flags


def _build_cmd(
    prompt: str,
    model: str,
    effort: str,
    system_prompt: str,
    allowed_tools: str,
    lean: bool = True,
) -> list[str]:
    """Build the cold-spawn claude CLI command array."""
    cmd = [
        "claude", "-p", prompt,
        "--model", model,
        "--no-session-persistence",
        "--output-format", "stream-json", "--verbose",
        *_boot_flags(model, allowed_tools, lean),
    ]
    if effort:
        cmd.extend(["--effort", effort])
    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])
    return cmd


# --- Concurrency gate ----------------------------------------------------------
# Each CLI is a heavyweight Node boot; on the shared host an unbounded burst stretches
# every call past its timeout. Code mode bypasses the gate (it can run for 30 min).
_gate_sem: asyncio.Semaphore | None = None
_gate_key: tuple | None = None


def _gate() -> asyncio.Semaphore:
    """Semaphore bound to the running loop; rebuilt when the size setting changes."""
    global _gate_sem, _gate_key
    key = (asyncio.get_running_loop(), max(1, config.CLAUDE_MAX_CONCURRENCY))
    if _gate_sem is None or _gate_key != key:
        _gate_sem, _gate_key = asyncio.Semaphore(key[1]), key
    return _gate_sem


# --- Subprocess helpers --------------------------------------------------------
async def _collect(proc) -> str:
    """Read stream-json until EOF, return the `result` text, then reap the process."""
    result_text = ""
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
            continue
        if event.get("type") == "result":
            result_text = event.get("result", "")
    # Reap inside the caller's timeout budget: a CLI that closes stdout but never exits
    # must not hang the turn.
    await proc.wait()
    return result_text


async def _reap(proc) -> None:
    """Kill + bounded-wait a process that is still alive (timeout, error, lingering)."""
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            pass


async def _run_cold(cmd: list[str], cwd: str, timeout: int, env: dict = _SUBPROCESS_ENV) -> str | None:
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            limit=_STREAM_LIMIT,
        )
        text = await asyncio.wait_for(_collect(proc), timeout=timeout)
        if not text and proc.returncode not in (0, None):
            err = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
            logger.warning(f"[claude_cli] Exit code {proc.returncode}: {err[:200]}")
        return text or None
    except asyncio.TimeoutError:
        logger.warning(f"[claude_cli] Timeout after {timeout}s, killing process")
        return None
    except FileNotFoundError as e:
        logger.warning(f"[claude_cli] FileNotFoundError: {e} (check binary PATH and cwd={cwd})")
        return None
    except Exception as e:
        logger.error(f"[claude_cli] Unexpected error: {e}")
        return None
    finally:
        await _reap(proc)


# --- Warm pool -----------------------------------------------------------------
# Pre-booted processes idle on stdin (`--input-format stream-json`, no prompt argv). A call
# claims one, sends its prompt as a stream-json user message, and skips CLI boot. Each process
# serves exactly ONE call. argv (model/effort/tools) is fixed at spawn, so the pool is keyed on
# it, and the call's real system prompt rides at the top of the user message.
_WARM_SYSTEM = (
    "You are the reasoning engine behind a companion character app. Each message begins with "
    "the ROLE AND RULES for that single request, followed by the conversation. Follow them "
    "exactly and reply with ONLY what they ask for."
)
_warm: dict[tuple, list[tuple]] = {}   # key -> [(proc, born_monotonic)]
_warm_pending: dict[tuple, int] = {}
_warm_loop = None


def _warm_key(model: str, effort: str, allowed_tools: str) -> tuple:
    return (model, effort or "", allowed_tools or "")


async def _spawn_warm(key: tuple):
    model, effort, tools = key
    argv = ["claude", "-p", "--input-format", "stream-json",
            "--output-format", "stream-json", "--verbose",
            "--model", model, "--no-session-persistence",
            *_boot_flags(model, tools),
            "--system-prompt", _WARM_SYSTEM]
    if effort:
        argv += ["--effort", effort]
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=common.NEUTRAL_CWD,
        env=_SUBPROCESS_ENV,
        limit=_STREAM_LIMIT,
    )


def _kill_nowait(proc) -> None:
    try:
        if proc.returncode is None:
            proc.kill()
    except Exception:
        pass


def _refill_pool(key: tuple) -> None:
    """Drop dead/stale procs for `key` and top the pool back up in the background."""
    global _warm_loop
    loop = asyncio.get_running_loop()
    if _warm_loop is not loop:  # fresh loop (tests / restart): old procs are orphans
        _warm.clear()
        _warm_pending.clear()
        _warm_loop = loop
    now = time.monotonic()
    procs = _warm.setdefault(key, [])
    fresh = []
    for proc, born in procs:
        if proc.returncode is not None:
            continue
        if now - born > config.CLAUDE_WARM_MAX_IDLE_S:  # stale auth / memory: recycle
            _kill_nowait(proc)
            continue
        fresh.append((proc, born))
    procs[:] = fresh
    missing = config.CLAUDE_WARM_POOL - len(procs) - _warm_pending.get(key, 0)

    async def _add():
        _warm_pending[key] = _warm_pending.get(key, 0) + 1
        try:
            proc = await _spawn_warm(key)
            _warm.setdefault(key, []).append((proc, time.monotonic()))
        except Exception:
            logger.warning("[claude_cli] warm spawn failed", exc_info=True)
        finally:
            _warm_pending[key] = max(0, _warm_pending.get(key, 1) - 1)

    for _ in range(max(0, missing)):
        asyncio.create_task(_add())


def _claim_warm(key: tuple):
    """Take one booted process for `key` (or None) and start the refill."""
    if config.CLAUDE_WARM_POOL <= 0:
        return None
    proc = None
    if _warm_loop is asyncio.get_running_loop():
        procs = _warm.get(key) or []
        now = time.monotonic()
        while procs and proc is None:
            cand, born = procs.pop(0)
            if cand.returncode is None and now - born <= config.CLAUDE_WARM_MAX_IDLE_S:
                proc = cand
            else:
                _kill_nowait(cand)
    _refill_pool(key)
    return proc


def prime_pool() -> int:
    """Pre-boot the pool for the smart-mode key at startup. Returns procs requested."""
    if config.LLM_PROVIDER != "claude" or config.CLAUDE_WARM_POOL <= 0:
        return 0
    key = _warm_key(config.CLAUDE_CLI_SMART_MODEL, config.CLAUDE_CLI_SMART_EFFORT,
                    config.CLAUDE_CLI_SMART_TOOLS)
    _refill_pool(key)
    logger.info(f"[claude_cli] priming warm pool key={key} size={config.CLAUDE_WARM_POOL}")
    return config.CLAUDE_WARM_POOL


def close_pool() -> None:
    """Kill every idle warm process (app shutdown)."""
    for procs in _warm.values():
        for proc, _ in procs:
            _kill_nowait(proc)
    _warm.clear()


async def _run_warm(proc, system_prompt: str, prompt: str, timeout: int) -> str | None:
    """Feed one prompt to a pre-booted process and read its single result."""
    body = f"ROLE AND RULES:\n{system_prompt}\n\n---\n\n{prompt}" if system_prompt else prompt
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": body}},
                     ensure_ascii=False) + "\n"
    try:
        proc.stdin.write(msg.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        return (await asyncio.wait_for(_collect(proc), timeout=timeout)) or None
    except asyncio.TimeoutError:
        logger.warning(f"[claude_cli] warm call timeout after {timeout}s")
        return None
    except Exception as e:
        logger.warning(f"[claude_cli] warm call failed: {e}")
        return None
    finally:
        await _reap(proc)


async def query_claude_cli(
    messages: list[dict],
    model: str = "haiku",
    effort: str = "low",
    timeout: int = 60,
    allowed_tools: str = "",
    cwd: str | None = None,
) -> str | None:
    """Run claude -p. Returns sanitized response text or None (caller falls back).

    cwd=None → lean spawn in the neutral cwd (gated, warm-pool eligible).
    cwd given (code mode sandbox) → ungated cold spawn that keeps the project config.
    """
    prompt, system_prompt = _build_prompt(messages)
    if not prompt:
        logger.warning("[claude_cli] No prompt to send")
        return None

    lean = cwd is None
    logger.info(f"[claude_cli] Running: model={model} effort={effort} timeout={timeout}s "
                f"tools={allowed_tools or 'none'} lean={lean}")

    warm = False
    queued = 0.0
    t0 = time.perf_counter()
    if lean:
        proc = _claim_warm(_warm_key(model, effort, allowed_tools))
        async with _gate():
            queued = time.perf_counter() - t0
            t1 = time.perf_counter()
            raw = None
            if proc is not None:
                warm = True
                raw = await _run_warm(proc, system_prompt, prompt, timeout)
                if raw is None:
                    logger.info("[claude_cli] warm miss, retrying cold")
                    warm = False
            if raw is None:
                cmd = _build_cmd(prompt, model, effort, system_prompt, allowed_tools, lean=True)
                raw = await _run_cold(cmd, common.NEUTRAL_CWD, timeout)
    else:
        t1 = time.perf_counter()
        cmd = _build_cmd(prompt, model, effort, system_prompt, allowed_tools, lean=False)
        raw = await _run_cold(cmd, cwd, timeout, env=_CODE_ENV)
    ran = time.perf_counter() - t1

    result = _sanitize_cli_response(raw) if raw else None
    common.record_perf(
        "cli" if lean else "code", model, effort=effort or "-", warm=warm,
        queued=queued, ran=ran, prompt_chars=len(prompt) + len(system_prompt),
        out_chars=len(raw or ""), ok=result is not None,
    )
    common.capture(model, system_prompt, prompt, raw)
    if raw:
        logger.info(f"[claude_cli] Response ({len(raw)} chars): {raw[:100]}...")
    else:
        logger.warning("[claude_cli] No result text from stream")
    return result


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
