"""run_shell — the companion runs shell commands.

Two backends (config.SHELL_BACKEND):
- docker (the server): commands execute inside the `avatar-shell` sidecar container
  (docker-compose.yml), which mounts ONLY the media HDD at /mnt/media, has no network
  and no docker socket. That container is the security boundary; this module never
  runs a host shell here (argv exec only).
- host (the PC harness): commands run on the machine the server runs on, in
  SHELL_ROOT — PowerShell on Windows, sh elsewhere. No isolation on this backend
  beyond the hard floor and the access tier (mid or above, chat turns only); it
  exists for a character that lives on the user's own PC and gets asked about it.

SDK-only on purpose: there is no prompts/tools doc and no tool_router route, so the
text-tag path and the Groq fallback providers never offer this tool. The
Claude SDK path sees every registered tool (claude_agent._resolve_sdk_tools).

Autonomous turns (idle talk, background checks, scheduled actions — user_name
"System") are refused: the shell only runs when the user asks in chat.

After a command that touches <NEXTCLOUD_DATA_DIR>/<user>/files, new files are
chowned to www-data and `occ files:scan` runs so they appear in Nextcloud.
"""

import asyncio
import logging
import os
import re
import shlex

import config
from services import tool_registry
from services import access, floors
from services.turn_context import is_autonomous

logger = logging.getLogger(__name__)

_MARKER = "/tmp/.run_shell_marker"
_WWW_DATA_UID = 33


def nextcloud_scan_paths(command: str) -> list[str]:
    """occ files:scan --path values for Nextcloud user dirs referenced by `command`.

    '/mnt/media/nextcloud/alice/files/notes/a.txt' -> '/alice/files/notes'
    (a file-looking last segment is scanned via its parent directory).
    """
    root = re.escape(config.NEXTCLOUD_DATA_DIR.rstrip("/"))
    paths = set()
    for m in re.finditer(root + r"/([^/\s'\";|&<>]+)/files((?:/[^\s'\";|&<>]*)?)", command):
        user, sub = m.group(1), m.group(2).rstrip("/")
        last = sub.rsplit("/", 1)[-1]
        if sub and "." in last and not last.startswith("."):
            sub = sub.rsplit("/", 1)[0]
        if "*" in sub or "?" in sub:
            sub = sub.split("*", 1)[0].split("?", 1)[0].rsplit("/", 1)[0]
        paths.add(f"/{user}/files{sub}")
    return sorted(paths)


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"...(truncated {len(text) - limit} chars)\n" + text[-limit:]


async def _exec(argv: list[str], timeout: float, cwd: str | None = None) -> tuple[int, str]:
    """Run argv (no shell) and return (exit code, combined output)."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=cwd,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "(timed out)"
    return proc.returncode, out.decode("utf-8", errors="replace")


def _in_shell(*args: str) -> list[str]:
    return ["docker", "exec", "-w", config.SHELL_ROOT, config.SHELL_CONTAINER, *args]


def _is_host() -> bool:
    return config.SHELL_BACKEND == "host"


def _host_argv(command: str) -> list[str]:
    """argv for the host backend. The timeout is enforced by _exec (no `timeout` wrapper)."""
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["sh", "-c", command]


def _describe() -> str:
    """Tool description per backend — the model must know which shell and which machine."""
    if _is_host():
        shell = "PowerShell on Windows" if os.name == "nt" else "POSIX sh"
        return (
            f"Run a shell command ({shell}) on the user's own PC — the machine you live on. "
            f"Working directory {config.SHELL_ROOT}. Use it when the user asks about this PC "
            "(RAM, CPU, disk space, running processes, uptime, files) or asks you to find, create, "
            "move or organize files here. 30 s limit, long output is truncated. Prefer read-only "
            "queries; never delete or modify anything unless the user explicitly asked."
        )
    return (
        "Run a shell command (POSIX sh, Debian coreutils/findutils) on the user's media hard drive, "
        "mounted at /mnt/media: movies/, music/, photos/, tv/ and nextcloud/<user>/files/ (the user's "
        "Nextcloud files). Use it when the user asks about their files or disk space, or asks you to "
        "find, create, move or organize files. No network, no docker, 30 s limit, long output is "
        "truncated. Use absolute paths; files written under nextcloud/<user>/files show up in "
        "Nextcloud automatically. Never delete anything unless the user explicitly asked."
    )


def _result(rc: int, out: str) -> dict:
    status = "timed out" if rc == 124 else f"exit {rc}"
    body = _tail(out.strip(), config.SHELL_MAX_OUTPUT) or "(no output)"
    logger.info(f"[run_shell] {status}, {len(out)} chars")
    # A non-zero exit (e.g. grep found nothing) is information, not a tool failure.
    return {"ok": True, "result": f"[{status}]\n{body}"}


async def _nextcloud_scan(path: str) -> None:
    rc, out = await _exec(
        ["docker", "exec", "-u", "www-data", config.NEXTCLOUD_CONTAINER,
         "php", "occ", "files:scan", f"--path={path}"],
        timeout=300,
    )
    logger.info(f"[run_shell] nextcloud scan {path} exit={rc} {out.strip()[-200:]}")


async def handle_run_shell(arg: str, context: dict) -> dict:
    command = (arg or "").strip()
    if not command:
        return {"ok": False, "result": "No command given."}
    # The floor comes first: it holds at every access level.
    floor = floors.check_command(command)
    if floor:
        logger.warning(f"[run_shell] floor refused ({floor}): {command[:200]}")
        return {"ok": False, "result": floors.refusal(floor)}
    if not config.SHELL_ENABLED:
        return {"ok": False, "result": "The shell is switched off (/set shell_enabled on)."}
    if is_autonomous(context or {}):
        return {"ok": False, "result": "run_shell only runs when the user asks for it in chat."}
    if not access.allows_ctx("shell", context):
        return {"ok": False, "result": "The shell is above the current access level (/set access_level mid)."}

    if _is_host():
        logger.info(f"[run_shell] (host) $ {command[:300]}")
        cwd = config.SHELL_ROOT if os.path.isdir(config.SHELL_ROOT) else None
        rc, out = await _exec(_host_argv(command), timeout=config.SHELL_TIMEOUT, cwd=cwd)
        return _result(rc, out)

    try:
        rc, _ = await _exec(_in_shell("mountpoint", "-q", config.SHELL_ROOT), timeout=10)
    except FileNotFoundError:
        return {"ok": False, "result": "docker CLI not available on this server."}
    if rc != 0:
        return {"ok": False, "result": f"The media drive isn't available ({config.SHELL_CONTAINER} down or HDD not mounted)."}

    touches_nextcloud = config.NEXTCLOUD_DATA_DIR.rstrip("/") in command
    if touches_nextcloud:
        await _exec(_in_shell("touch", _MARKER), timeout=10)

    logger.info(f"[run_shell] $ {command[:300]}")
    timeout = config.SHELL_TIMEOUT
    # `timeout` runs INSIDE the container: killing the docker exec client alone would
    # leave the command running in the sidecar.
    rc, out = await _exec(
        _in_shell("timeout", "-k", "5", str(timeout), "sh", "-c", command),
        timeout=timeout + 15,
    )

    if touches_nextcloud:
        nc = shlex.quote(config.NEXTCLOUD_DATA_DIR.rstrip("/"))
        await _exec(_in_shell(
            "sh", "-c",
            f"find {nc}/*/files -newer {_MARKER} ! -user {_WWW_DATA_UID} "
            f"-exec chown {_WWW_DATA_UID}:{_WWW_DATA_UID} {{}} +",
        ), timeout=60)
        for path in nextcloud_scan_paths(command):
            asyncio.create_task(_nextcloud_scan(path))

    return _result(rc, out)


_ARG_EXAMPLE = (
    "'Get-CimInstance Win32_OperatingSystem | Select FreePhysicalMemory,TotalVisibleMemorySize'"
    if _is_host() and os.name == "nt" else "'df -h /mnt/media' or 'ls -la /mnt/media/photos'"
)

tool_registry.register("run_shell", handle_run_shell, schema={
    "name": "run_shell",
    "description": _describe(),
    "parameters": {
        "type": "object",
        "properties": {
            "arg": {
                "type": "string",
                "description": f"The shell command, e.g. {_ARG_EXAMPLE}",
            }
        },
        "required": ["arg"],
    },
})
