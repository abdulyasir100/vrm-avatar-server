"""Habit tracker storage — SQLite with streak tracking."""

import logging
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None
_lock = Lock()

WIB = timezone(timedelta(hours=7))


def init(db_path: str):
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS habits (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                archived INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id TEXT NOT NULL,
                date TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                FOREIGN KEY (habit_id) REFERENCES habits(id),
                UNIQUE(habit_id, date)
            );
        """)
    logger.info(f"[habit] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _today() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d")


def add_habit(name: str) -> dict:
    habit_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO habits (id, name, created_at) VALUES (?, ?, ?)",
            (habit_id, name, now),
        )
    return {"id": habit_id, "name": name}


def get_all(include_archived: bool = False) -> list[dict]:
    query = "SELECT * FROM habits"
    if not include_archived:
        query += " WHERE archived = 0"
    query += " ORDER BY created_at"
    with _lock, _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def get_by_id(habit_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM habits WHERE id = ?", (habit_id,)).fetchone()
    return dict(row) if row else None


def find_by_name(name: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM habits WHERE archived = 0 AND LOWER(name) LIKE ?",
            (f"%{name.lower()}%",),
        ).fetchone()
    return dict(row) if row else None


def complete_today(habit_id: str) -> bool:
    today = _today()
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO completions (habit_id, date, completed_at) VALUES (?, ?, ?)",
                (habit_id, today, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False  # Already completed today


def is_completed_today(habit_id: str) -> bool:
    today = _today()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM completions WHERE habit_id = ? AND date = ?",
            (habit_id, today),
        ).fetchone()
    return row is not None


def get_streak(habit_id: str) -> int:
    """Count consecutive days completed up to today."""
    today = datetime.now(WIB).date()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT date FROM completions WHERE habit_id = ? ORDER BY date DESC",
            (habit_id,),
        ).fetchall()

    if not rows:
        return 0

    dates = [datetime.strptime(r["date"], "%Y-%m-%d").date() for r in rows]
    streak = 0
    expected = today

    for d in dates:
        if d == expected:
            streak += 1
            expected -= timedelta(days=1)
        elif d < expected:
            break

    return streak


def get_habit_status(habit_id: str) -> dict:
    """Get habit with today's completion and streak."""
    habit = get_by_id(habit_id)
    if not habit:
        return {}
    return {
        **habit,
        "done_today": is_completed_today(habit_id),
        "streak": get_streak(habit_id),
    }


def get_all_with_status() -> list[dict]:
    habits = get_all()
    return [get_habit_status(h["id"]) for h in habits]


def get_incomplete_today() -> list[dict]:
    """Get habits not yet completed today."""
    habits = get_all()
    return [
        get_habit_status(h["id"])
        for h in habits
        if not is_completed_today(h["id"])
    ]


def archive(habit_id: str) -> bool:
    with _lock, _connect() as conn:
        cursor = conn.execute("UPDATE habits SET archived = 1 WHERE id = ?", (habit_id,))
    return cursor.rowcount > 0
