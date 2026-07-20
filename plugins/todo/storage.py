"""Todo plugin — SQLite storage for tasks."""

import logging
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)
_DB_PATH: Path | None = None
_lock = Lock()


def init(db_path: str):
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                completed INTEGER DEFAULT 0,
                priority TEXT DEFAULT 'medium',
                category TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                due_time TEXT,
                reminder_offsets TEXT DEFAULT '[10, 5]',
                recurrence TEXT,
                recurrence_time TEXT,
                last_generated TEXT,
                source TEXT DEFAULT ''
            );
        """)
    logger.info(f"[todo] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row) -> dict:
    import json
    d = dict(row)
    d["completed"] = bool(d["completed"])
    d["reminder_offsets"] = json.loads(d.get("reminder_offsets") or "[]")
    return d


def create(title: str, priority: str = "medium", category: str = "",
           due_time: str | None = None, source: str = "") -> dict:
    task_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO tasks (id, title, priority, category, created_at, due_time, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, title, priority, category, now, due_time, source),
        )
    return {"id": task_id, "title": title, "priority": priority, "due_time": due_time}


def get_all(completed: bool | None = None, priority: str | None = None,
            category: str | None = None) -> list[dict]:
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if completed is not None:
        query += " AND completed = ?"
        params.append(int(completed))
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC"

    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_by_id(task_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def complete(task_id: str) -> dict | None:
    with _lock, _connect() as conn:
        conn.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (task_id,))
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def delete(task_id: str) -> bool:
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return cursor.rowcount > 0


def update(task_id: str, **kwargs) -> dict | None:
    if not kwargs:
        return get_by_id(task_id)
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [task_id]
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", vals)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_upcoming_reminders(within_minutes: int = 15) -> list[dict]:
    """Get tasks with due_time within N minutes from now."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=within_minutes)
    with _lock, _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks
               WHERE completed = 0 AND due_time IS NOT NULL
               AND due_time > ? AND due_time <= ?""",
            (now.isoformat(), cutoff.isoformat()),
        ).fetchall()

    reminders = []
    for row in rows:
        task = _row_to_dict(row)
        due = datetime.fromisoformat(task["due_time"])
        minutes_until = int((due - now).total_seconds() / 60)
        for offset in task.get("reminder_offsets", [10, 5]):
            if minutes_until <= offset:
                reminders.append({
                    "task_id": task["id"],
                    "title": task["title"],
                    "due_time": task["due_time"],
                    "minutes_until_due": minutes_until,
                })
                break
    return reminders


def get_overdue() -> list[dict]:
    """Get non-completed tasks past their due time."""
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks
               WHERE completed = 0 AND due_time IS NOT NULL AND due_time < ?
               AND source != 'prayer' AND category != 'prayer'""",
            (now,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
