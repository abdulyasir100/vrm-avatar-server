"""Gym plugin storage — workout plans and completion tracking."""

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None
_lock = Lock()

WIB = timezone(timedelta(hours=7))

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def init(db_path: str):
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS profile (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plan (
                day INTEGER PRIMARY KEY,
                type TEXT NOT NULL,
                exercises TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                notes TEXT,
                completed_at TEXT NOT NULL
            );
        """)
    logger.info(f"[gym] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _today() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d")


def _today_weekday() -> int:
    return datetime.now(WIB).weekday()


# --- Profile ---

def set_profile(key: str, value: str):
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile (key, value) VALUES (?, ?)",
            (key, value),
        )


def get_profile(key: str) -> str | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_all_profile() -> dict:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT key, value FROM profile").fetchall()
    return {r["key"]: r["value"] for r in rows}


def has_plan() -> bool:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM plan").fetchone()
    return row["c"] > 0


# --- Plan ---

def save_plan(days: list[dict]):
    """Save a 7-day plan. Each dict: {day: 0-6, type: str, exercises: list[dict]}"""
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM plan")
        for d in days:
            conn.execute(
                "INSERT INTO plan (day, type, exercises) VALUES (?, ?, ?)",
                (d["day"], d["type"], json.dumps(d["exercises"])),
            )


def get_plan() -> list[dict]:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT * FROM plan ORDER BY day").fetchall()
    result = []
    for r in rows:
        result.append({
            "day": r["day"],
            "day_name": WEEKDAYS[r["day"]],
            "type": r["type"],
            "exercises": json.loads(r["exercises"]),
        })
    return result


def get_today_plan() -> dict | None:
    day = _today_weekday()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM plan WHERE day = ?", (day,)).fetchone()
    if not row:
        return None
    return {
        "day": row["day"],
        "day_name": WEEKDAYS[row["day"]],
        "type": row["type"],
        "exercises": json.loads(row["exercises"]),
    }


# --- Completions ---

def is_completed_today() -> bool:
    today = _today()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT 1 FROM completions WHERE date = ?", (today,)).fetchone()
    return row is not None


def complete_today(notes: str = "") -> bool:
    today = _today()
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO completions (date, notes, completed_at) VALUES (?, ?, ?)",
                (today, notes, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_week_completions() -> list[str]:
    """Get completed dates for this week."""
    today = datetime.now(WIB).date()
    monday = today - timedelta(days=today.weekday())
    dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT date FROM completions WHERE date IN ({})".format(
                ",".join("?" for _ in dates)
            ),
            dates,
        ).fetchall()
    return [r["date"] for r in rows]


def get_streak() -> int:
    """Count consecutive workout days (skipping rest days)."""
    plan = get_plan()
    if not plan:
        return 0

    rest_days = {d["day"] for d in plan if d["type"].lower() == "rest"}
    today = datetime.now(WIB).date()

    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT date FROM completions ORDER BY date DESC LIMIT 30"
        ).fetchall()

    completed = {r["date"] for r in rows}
    streak = 0
    check = today

    for _ in range(30):
        if check.weekday() in rest_days:
            check -= timedelta(days=1)
            continue
        if check.strftime("%Y-%m-%d") in completed:
            streak += 1
            check -= timedelta(days=1)
        else:
            break

    return streak


def reset():
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM plan")
        conn.execute("DELETE FROM profile")
        conn.execute("DELETE FROM completions")
