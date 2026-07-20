"""Dashboard stats — read-only metrics aggregator.

Parses the existing log files (logs/all.log, logs/chat.log, logs/error.log
plus their RotatingFileHandler backups) and SQLite (data/memory.db) to
produce metrics for the /dashboard endpoint. No new tables, no new
instrumentation — every number here can be re-derived from the raw log
or DB row, which keeps this service replaceable.

Window arg is "24h" | "7d" | "all".
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone

from utils import time as ltime
from pathlib import Path
from typing import Iterable

import config
from services import plugin_loader

logger = logging.getLogger(__name__)


_LOG_DIR = Path("logs")
_DB_PATH = Path(config.MEMORY_DB_PATH)

# Sandbox apps whose git activity feeds the dashboard. Configure via the
# TRACKED_APPS env var as JSON: '[{"name": "my-app", "path": "/abs/path"}]'.
# Empty by default — a fresh install tracks nothing until you point it at apps.
def _load_tracked_apps() -> list[dict]:
    import json
    raw = os.environ.get("TRACKED_APPS", "").strip()
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except Exception:
        return []

_TRACKED_APPS = _load_tracked_apps()

# --- Log line parsers ---------------------------------------------------------

# Format produced by main.py file handlers:
#   2026-05-08 14:54:08 [INFO] chat: [telegram] Venomaru: hi
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\]")
# chat.log specific bits:
_TOOL_RE = re.compile(r"chat: \[(?P<context>[^\]]+)\] Tool: (?P<tool>\w+)\(")
_ASSISTANT_RE = re.compile(r"chat: \[(?P<context>[^\]]+)\] [^:]+ \[(?P<emotion>[A-Z]+)\]:")
# Two paths a /p.* command can take into the logs:
#   1) user pastes the literal slash command in chat — matched by the
#      [telegram] User: /p.foo pattern below
#   2) telegram bot routes through POST /plugin/command — routers/plugin.py
#      emits [plugin_cmd] /p.foo into chat.log, matched by the second pattern
_USER_CMD_RE = re.compile(r"chat: \[telegram\] [^:]+: (?P<cmd>/p\.[\w.]+)")
_PLUGIN_CMD_RE = re.compile(r"chat: \[plugin_cmd\] (?P<cmd>/p\.[\w.]+)")
# all.log specific bits:
_FALLBACK_RE = re.compile(r"\[llm\] Fallback (?P<provider>\w+) succeeded")
_WAKE_RE = re.compile(r"\[sleep\] Woke up \((?P<reason>[^)]+)\)")


def _window_cutoff(window: str) -> datetime | None:
    """Return the lower-bound datetime (UTC-naive, matching log file timestamps).

    `all` returns None — no cutoff, include everything.
    """
    now = datetime.now()
    if window == "24h":
        return now - timedelta(hours=24)
    if window == "7d":
        return now - timedelta(days=7)
    return None


def _rotating_files(base: Path) -> list[Path]:
    """Return [base, base.1, base.2, ...] for files that exist, oldest last."""
    if not base.exists():
        return []
    return [base] + sorted(base.parent.glob(f"{base.name}.*"))


def _iter_log_lines(base: Path, cutoff: datetime | None) -> Iterable[str]:
    """Yield lines from a log file (and its rotated backups) within the window.

    Stops walking older files as soon as a file's last line is past the cutoff
    being too old — keeps the parser cheap for short windows.
    """
    for path in _rotating_files(base):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if cutoff is None:
                        yield line
                        continue
                    m = _LOG_TS_RE.match(line)
                    if not m:
                        # Non-conforming line (e.g. multi-line stack trace continuation).
                        # Keep yielding — the parser will simply not match a metric pattern.
                        yield line
                        continue
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    if ts >= cutoff:
                        yield line
        except OSError as e:
            logger.warning(f"[dashboard_stats] failed to read {path}: {e}")


# --- Metric functions ---------------------------------------------------------


def tool_usage(window: str) -> dict:
    """Histogram of {tool_name: count} parsed from chat.log Tool: lines."""
    counts: Counter[str] = Counter()
    for line in _iter_log_lines(_LOG_DIR / "chat.log", _window_cutoff(window)):
        m = _TOOL_RE.search(line)
        if m:
            counts[m.group("tool")] += 1
    return dict(counts.most_common())


def idle_categories(window: str) -> dict:
    """Distribution of assistant message contexts (idle/scheduled/telegram/etc.).

    Parses the [<context>] label in chat.log assistant lines. Anything that
    isn't 'telegram' counts as autonomous output (idle/scheduled/plugin).
    """
    counts: Counter[str] = Counter()
    for line in _iter_log_lines(_LOG_DIR / "chat.log", _window_cutoff(window)):
        m = _ASSISTANT_RE.search(line)
        if m:
            counts[m.group("context")] += 1
    return dict(counts.most_common())


def error_rate(window: str) -> dict:
    """{total, by_level, fallback_hits} parsed from error.log + all.log."""
    cutoff = _window_cutoff(window)
    by_level: Counter[str] = Counter()
    fallback_hits: Counter[str] = Counter()
    sleep_wakeups = 0

    for line in _iter_log_lines(_LOG_DIR / "error.log", cutoff):
        m = _LOG_TS_RE.match(line)
        if m:
            by_level[m.group(2)] += 1

    for line in _iter_log_lines(_LOG_DIR / "all.log", cutoff):
        fb = _FALLBACK_RE.search(line)
        if fb:
            fallback_hits[fb.group("provider")] += 1
        if _WAKE_RE.search(line):
            sleep_wakeups += 1

    return {
        "total": sum(by_level.values()),
        "by_level": dict(by_level),
        "fallback_hits": dict(fallback_hits),
        "sleep_wakeups": sleep_wakeups,
    }


def mood_trend(window: str) -> dict:
    """Current mood + emotion histogram of assistant messages in window."""
    cutoff_iso = _cutoff_iso(window)
    emotions: Counter[str] = Counter()
    current = None
    try:
        with sqlite3.connect(str(_DB_PATH)) as conn:
            row = conn.execute("SELECT value FROM mood ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                current = float(row[0])
            if cutoff_iso is None:
                rows = conn.execute(
                    "SELECT emotion FROM conversations WHERE role='assistant' AND emotion IS NOT NULL"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT emotion FROM conversations "
                    "WHERE role='assistant' AND emotion IS NOT NULL AND timestamp >= ?",
                    (cutoff_iso,),
                ).fetchall()
            for (e,) in rows:
                if e:
                    emotions[e] += 1
    except sqlite3.Error as e:
        logger.warning(f"[dashboard_stats] mood_trend DB error: {e}")
    return {
        "current": current,
        "bracket": _mood_bracket(current),
        "emotions": dict(emotions.most_common()),
    }


def plugin_commands(window: str) -> dict:
    """Histogram of /p.* commands invoked from chat.log.

    Seeded with all enabled plugin commands so plugins that never fired
    show up as zero (which is itself the actionable signal).
    """
    counts: Counter[str] = Counter()
    # Seed zeros from manifests so deprecated/unused plugins are visible.
    try:
        for entry in plugin_loader.get_enabled_plugins().values():
            for cmd in entry["manifest"].get("telegram_commands", []):
                counts[f"/p.{cmd['command']}"] = 0
    except Exception as e:
        logger.warning(f"[dashboard_stats] plugin enumeration failed: {e}")

    for line in _iter_log_lines(_LOG_DIR / "chat.log", _window_cutoff(window)):
        m = _USER_CMD_RE.search(line) or _PLUGIN_CMD_RE.search(line)
        if m:
            counts[m.group("cmd")] += 1

    # Sort: actually-invoked first (desc), then zero-counts alphabetically.
    used = {k: v for k, v in counts.items() if v > 0}
    unused = {k: v for k, v in counts.items() if v == 0}
    return {
        **dict(sorted(used.items(), key=lambda kv: -kv[1])),
        **dict(sorted(unused.items())),
    }


def conversation_volume(window: str) -> dict:
    """Message counts (user vs assistant) and active-day count from memory.db."""
    cutoff_iso = _cutoff_iso(window)
    user_msgs = 0
    assistant_msgs = 0
    active_days = 0
    try:
        with sqlite3.connect(str(_DB_PATH)) as conn:
            if cutoff_iso is None:
                user_msgs = conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE role='user'"
                ).fetchone()[0]
                assistant_msgs = conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE role='assistant'"
                ).fetchone()[0]
                active_days = conn.execute(
                    "SELECT COUNT(DISTINCT DATE(timestamp, '+7 hours')) FROM conversations"
                ).fetchone()[0]
            else:
                user_msgs = conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE role='user' AND timestamp >= ?",
                    (cutoff_iso,),
                ).fetchone()[0]
                assistant_msgs = conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE role='assistant' AND timestamp >= ?",
                    (cutoff_iso,),
                ).fetchone()[0]
                active_days = conn.execute(
                    "SELECT COUNT(DISTINCT DATE(timestamp, '+7 hours')) "
                    "FROM conversations WHERE timestamp >= ?",
                    (cutoff_iso,),
                ).fetchone()[0]
    except sqlite3.Error as e:
        logger.warning(f"[dashboard_stats] conversation_volume DB error: {e}")

    return {
        "user_msgs": user_msgs,
        "assistant_msgs": assistant_msgs,
        "active_days": active_days,
    }


def app_activity(window: str) -> list[dict]:
    """Per-app commit count + last commit info via `git log` on tracked sandboxes."""
    cutoff = _window_cutoff(window)
    since_arg = []
    if cutoff is not None:
        since_arg = ["--since", cutoff.strftime("%Y-%m-%d %H:%M:%S")]

    out = []
    for app in _TRACKED_APPS:
        path = Path(app["path"])
        entry = {"app": app["name"], "path": str(path), "commits": 0,
                 "last_commit": None, "last_commit_msg": None}
        if not path.exists():
            entry["error"] = "path missing"
            out.append(entry)
            continue
        try:
            # -c safe.directory=*: container runs as root but the bind-mounted
            # sandbox is owned by uid 1000, which trips git's dubious-ownership
            # check. The sandbox path is hardcoded in _TRACKED_APPS so this is
            # not user-controlled.
            git_safe = ["git", "-c", "safe.directory=*"]
            count = subprocess.run(
                [*git_safe, "rev-list", "--count", "HEAD", *since_arg],
                cwd=path, capture_output=True, text=True, timeout=5,
            )
            if count.returncode == 0:
                entry["commits"] = int(count.stdout.strip() or 0)
            last = subprocess.run(
                [*git_safe, "log", "-1", "--format=%h|%ai|%s"],
                cwd=path, capture_output=True, text=True, timeout=5,
            )
            if last.returncode == 0 and last.stdout.strip():
                sha, ai, msg = last.stdout.strip().split("|", 2)
                entry["last_commit"] = f"{sha} {ai}"
                entry["last_commit_msg"] = msg
        except (subprocess.TimeoutExpired, ValueError, OSError) as e:
            entry["error"] = str(e)
        out.append(entry)
    return out


# --- Helpers ------------------------------------------------------------------


def _cutoff_iso(window: str) -> str | None:
    """Cutoff as UTC ISO string for SQLite timestamp comparisons.

    The conversations table stores timestamps in UTC (CURRENT_TIMESTAMP), so the
    cutoff must be UTC too — not local time.
    """
    if window == "24h":
        return (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    if window == "7d":
        return (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    return None


def _mood_bracket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 90:
        return "adoring"
    if value >= 70:
        return "normal"
    if value >= 50:
        return "sassy"
    if value >= 30:
        return "reluctant"
    return "defiant"


# Credit background_task / background_check / scheduled_actions firings to the
# plugin that produced them, so the scoreboard doesn't show heavily-active
# plugins as "dead" just because they have no foreground tool/command calls.
# The category → plugin map is built from manifests (a plugin's optional
# "idle_categories" list), so no plugin is hardcoded here.
def _owner_for_context(ctx: str) -> str | None:
    owners = plugin_loader.get_idle_category_owners()
    if ctx in owners:
        return owners[ctx]
    # Fallback pattern: "<plugin>_check" (the default in the background loop).
    if ctx.endswith("_check"):
        return ctx[: -len("_check")]
    return None


def plugin_scoreboard(window: str) -> list[dict]:
    """Per-plugin activity rollup — sums each plugin's tool calls, command
    invocations, AND background events (idle/scheduled firings) into a single
    score. Sorted dead-first so kill candidates surface immediately.

    Status:
      - active: any tool_calls / command_calls / background_events > 0
      - dead: zero across all three AND plugin exposes user-facing tools or commands
      - background_only: zero across all three AND no user-facing surface
    """
    tools = tool_usage(window)
    commands = plugin_commands(window)
    idle_cats = idle_categories(window)

    # Pre-bucket idle categories by owner plugin.
    bg_by_plugin: Counter[str] = Counter()
    for ctx, count in idle_cats.items():
        owner = _owner_for_context(ctx)
        if owner:
            bg_by_plugin[owner] += count

    out: list[dict] = []
    try:
        for name, entry in plugin_loader.get_enabled_plugins().items():
            manifest = entry["manifest"]
            tool_names = [t["name"] for t in manifest.get("tools", [])]
            cmd_names = [f"/p.{c['command']}" for c in manifest.get("telegram_commands", [])]
            tool_calls = sum(tools.get(t, 0) for t in tool_names)
            command_calls = sum(commands.get(c, 0) for c in cmd_names)
            background_events = bg_by_plugin.get(name, 0)
            score = tool_calls + command_calls + background_events
            has_surface = bool(tool_names or cmd_names)
            if score > 0:
                status = "active"
            elif has_surface:
                status = "dead"
            else:
                status = "background_only"
            out.append({
                "plugin": name,
                "tool_calls": tool_calls,
                "command_calls": command_calls,
                "background_events": background_events,
                "score": score,
                "status": status,
                "tools": tool_names,
                "commands": cmd_names,
            })
    except Exception as e:
        logger.warning(f"[dashboard_stats] plugin_scoreboard failed: {e}")
        return []

    # Sort: dead first (kill list), then background_only, then active by score asc.
    status_rank = {"dead": 0, "background_only": 1, "active": 2}
    out.sort(key=lambda p: (status_rank[p["status"]], p["score"], p["plugin"]))
    return out


def user_state_snapshot() -> dict:
    """Current cached user-state snapshot from services/user_state.py."""
    try:
        from services import user_state
        return user_state.get_state()
    except Exception as e:
        logger.warning(f"[dashboard_stats] user_state read failed: {e}")
        return {}


def collect_all(window: str) -> dict:
    """Aggregate every metric. Used by GET /dashboard."""
    return {
        "window": window,
        "generated_at": ltime.now().isoformat(timespec="seconds"),
        "tool_usage": tool_usage(window),
        "idle_categories": idle_categories(window),
        "error_rate": error_rate(window),
        "mood_trend": mood_trend(window),
        "plugin_commands": plugin_commands(window),
        "plugin_scoreboard": plugin_scoreboard(window),
        "conversation_volume": conversation_volume(window),
        "app_activity": app_activity(window),
        "user_state": user_state_snapshot(),
    }
