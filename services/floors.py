"""The hard floor — commands refused at EVERY access tier, administrator included.

Tiers (services/access.py) decide what she may reach; the floor is what nobody
reaches. It is evaluated first, against a de-obfuscated form of the command, so
quoting tricks (r\\m, r""m, ${IFS}) don't slip past.

Two consumers, one table:
- run_shell calls check_command() in-process.
- Code mode runs the Claude CLI with this file as a PreToolUse hook (hook_settings()),
  so its Bash/Write/Edit calls hit the same table. Hence: stdlib only, no package
  imports — the hook executes this file as a plain script.

A floor is a seatbelt against a confused or prompt-injected model, not a sandbox:
a determined adversary with a shell can always encode around a denylist. Isolation
(containers, the docker allowlist proxy) is what actually confines her.
"""

import json
import re
import shlex
import sys

# Roots whose recursive removal / chmod / chown is never legitimate.
_ROOTS = r"(?:/|~|\$home|/home(?:/[\w.-]+)?|/root|/etc|/usr|/var|/mnt|/mnt/media|/app)"
_ROOT_TARGET = rf"(?:^|\s){_ROOTS}/?\*?(?=\s|$|[;&|])"
# Windows host backend: a bare drive root (normalize() strips the backslash, so
# `C:\` arrives as `c:`) or the profile/home variables.
_WIN_ROOT_TARGET = r"(?:^|\s)(?:[a-z]:/?|\$env:(?:userprofile|systemdrive|systemroot|windir)|\$home)\*?(?=\s|$|[;&|])"

# (reason, regex) — matched with re.search against normalize(command).
COMMAND_FLOORS = tuple((reason, re.compile(rx)) for reason, rx in (
    ("recursive delete of a root directory",
     rf"\brm\b(?=[^;&|]*\s-\w*r)[^;&|]*{_ROOT_TARGET}"),
    ("recursive delete of a root directory",
     rf"\brm\b(?=[^;&|]*\s--recursive)[^;&|]*{_ROOT_TARGET}"),
    ("recursive chmod/chown of a root directory",
     rf"\bch(?:mod|own)\b(?=[^;&|]*\s-\w*r)[^;&|]*{_ROOT_TARGET}"),
    ("formatting or partitioning a disk", r"\b(?:mkfs(?:\.\w+)?|wipefs|fdisk|parted|sgdisk)\b"),
    ("raw write to a block device", r"\bdd\b[^;&|]*\bof=/dev/|>\s*/dev/(?:sd|nvme|vd|hd|mmcblk)"),
    ("fork bomb", r":\s*\(\s*\)\s*\{"),
    ("powering off or rebooting the host",
     # command position only — "fix reboot bug" in a commit message is not a reboot
     r"(?:^|[;&|(]\s*)(?:shutdown|reboot|poweroff|halt|init\s+[06])\b|\bsystemctl\s+(?:poweroff|reboot|halt|kexec)\b"),
    ("piping a download into a shell", r"\b(?:curl|wget)\b[^;&]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b"),
    ("reaching for the Docker socket or the host namespace",
     r"docker\.sock|\bnsenter\b|\bchroot\s+/host\b"),
    # --- Windows / PowerShell equivalents (SHELL_BACKEND=host on the PC harness) ---
    ("recursive delete of a drive root",
     rf"\b(?:remove-item|ri|del|erase|rd|rmdir)\b(?=[^;&|]*\s(?:-recurse|-r|/s)\b)[^;&|]*{_WIN_ROOT_TARGET}"),
    ("formatting or partitioning a disk",
     r"\b(?:format-volume|clear-disk|initialize-disk|remove-partition|diskpart)\b|(?:^|[;&|(]\s*)format\s+[a-z]:"),
    ("powering off or rebooting the host", r"\b(?:stop-computer|restart-computer)\b"),
    ("reading credentials",
     r"\.credentials\.json|/etc/shadow|\bid_(?:rsa|ed25519|ecdsa)\b|/app/\.env\b|\.claude\.json"),
))

# Write/Edit targets that are never legitimate (matched against the raw path).
PATH_FLOORS = tuple((reason, re.compile(rx)) for reason, rx in (
    ("writing credentials or agent config", r"(?:^|/)\.claude(?:\.json|/)|\.credentials\.json|(?:^|/)\.ssh/"),
    ("writing system files", r"^/(?:etc|usr|bin|sbin|boot|lib\w*|var/run)/"),
    ("writing the server's own code or secrets", r"^/app/"),
))

_WRAPPERS = re.compile(r"(?:^|(?<=[;&|(]))\s*(?:(?:sudo(?:\s+-\w+)*|command|builtin|exec|env|nohup|time)\s+)+")


def normalize(command: str) -> str:
    """Lowercase, de-obfuscated, single-spaced form used for floor matching only."""
    s = command.lower()
    s = re.sub(r"\$\{?ifs\}?", " ", s)        # ${IFS} word-splitting trick
    s = s.replace("\\\n", "")                  # line continuations
    s = re.sub(r"[\"'`\\]", "", s)             # r""m, r\m, 'rm'
    s = re.sub(r"\s+", " ", s).strip()
    return _WRAPPERS.sub(" ", s).strip()       # sudo rm == rm


def check_command(command: str) -> str | None:
    """Reason this command is refused, or None."""
    norm = normalize(command or "")
    for reason, rx in COMMAND_FLOORS:
        if rx.search(norm):
            return reason
    return None


def check_path(path: str) -> str | None:
    for reason, rx in PATH_FLOORS:
        if rx.search((path or "").replace("\\", "/")):
            return reason
    return None


def check_tool(tool_name: str, tool_input: dict) -> str | None:
    """Floor check for one Claude CLI tool call (PreToolUse payload)."""
    if tool_name == "Bash":
        return check_command(tool_input.get("command", ""))
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return check_path(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    return None


def refusal(reason: str) -> str:
    return f"Refused by the hard floor ({reason}). This is blocked at every access level; do not retry or rephrase it."


def hook_settings() -> str:
    """`claude --settings` JSON that runs this file before every tool call."""
    command = f"{shlex.quote(sys.executable)} {shlex.quote(__file__)}"
    return json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 10}]},
    ]}})


def _hook_main() -> int:
    """PreToolUse hook entry: exit 2 + stderr blocks the call and tells the model why."""
    try:
        payload = json.load(sys.stdin)
        reason = check_tool(payload.get("tool_name", ""), payload.get("tool_input") or {})
    except Exception as e:  # a broken hook must fail CLOSED
        reason = f"floor check failed: {e}"
    if reason:
        print(refusal(reason), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_hook_main())
