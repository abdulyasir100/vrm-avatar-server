"""Clock-in plugin storage — state persistence via SQLite."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_db_path: Path | None = None
_conn: sqlite3.Connection | None = None


def init(db_path: str):
    global _db_path, _conn
    _db_path = Path(db_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(_db_path))
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    _conn.commit()
    logger.info(f"[clockin] Storage initialized at {db_path}")


def _get(key: str, default=None):
    row = _conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    return default


def _set(key: str, value):
    _conn.execute(
        "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
        (key, json.dumps(value)),
    )
    _conn.commit()


def is_enabled() -> bool:
    return _get("enabled", False)


def set_enabled(val: bool):
    _set("enabled", val)


def get_today_state() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    state = _get("today_state", {})
    if state.get("date") != today:
        return {"date": today}
    return state


def save_today_state(state: dict):
    _set("today_state", state)
