"""Google plugin storage: actions that wait for a human tap before they touch the account."""

import json
import sqlite3
from contextlib import contextmanager

_DB_PATH = None


def init(db_path: str) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,             -- mail | event
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending | working | done | discarded
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")


@contextmanager
def _connect():
    conn = sqlite3.connect(_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _action(row) -> dict:
    return dict(row, payload=json.loads(row["payload"]))


def add_action(kind: str, payload: dict) -> int:
    with _connect() as conn:
        return conn.execute("INSERT INTO actions (kind, payload) VALUES (?, ?)", (kind, json.dumps(payload))).lastrowid


def pending_actions() -> list[dict]:
    with _connect() as conn:
        return [_action(r) for r in conn.execute("SELECT * FROM actions WHERE status = 'pending' ORDER BY id")]


def take_action(action_id: int) -> dict | None:
    """Claim a pending action (pending -> working). None if it is gone or someone else has it."""
    with _connect() as conn:
        claimed = conn.execute("UPDATE actions SET status = 'working' WHERE id = ? AND status = 'pending'", (action_id,))
        if not claimed.rowcount:
            return None
        return _action(conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone())


def finish_action(action_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE actions SET status = ? WHERE id = ?", (status, action_id))
