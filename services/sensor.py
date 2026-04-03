"""Sensor data storage and queries — screen time + step count from Companion Sensor app."""

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)
_DB_PATH: Path | None = None
_lock = Lock()


def init(db_path: str = "data/memory.db"):
    global _DB_PATH
    _DB_PATH = Path(db_path)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sensor_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_json TEXT NOT NULL DEFAULT '{}',
                timestamp TEXT NOT NULL,
                received_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_sensor_ts ON sensor_data(timestamp);
        """)
    logger.info("[sensor] Initialized")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def store(data: dict, timestamp: str):
    """Store a sensor payload."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO sensor_data (data_json, timestamp) VALUES (?, ?)",
            (json.dumps(data), timestamp or datetime.now().isoformat()),
        )


def get_latest() -> dict | None:
    """Get the most recent sensor reading."""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT data_json, timestamp FROM sensor_data ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    data = json.loads(row["data_json"])
    data["_timestamp"] = row["timestamp"]
    return data


def get_today_summary() -> dict | None:
    """Get today's latest sensor reading (steps are cumulative, screen_time is cumulative)."""
    wib = timezone(timedelta(hours=7))
    today_start = datetime.now(wib).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT data_json, timestamp FROM sensor_data WHERE timestamp >= ? ORDER BY id DESC LIMIT 1",
            (today_start,),
        ).fetchone()
    if not row:
        return None
    data = json.loads(row["data_json"])
    data["_timestamp"] = row["timestamp"]
    return data


_DEFAULT_STEP_GOAL = 7000


def get_step_goal() -> int:
    """Get the daily step goal."""
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        row = conn.execute("SELECT value FROM sensor_settings WHERE key = 'step_goal'").fetchone()
    return int(row["value"]) if row else _DEFAULT_STEP_GOAL


def set_step_goal(goal: int):
    """Set the daily step goal."""
    with _lock, _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO sensor_settings (key, value) VALUES ('step_goal', ?)",
            (str(goal),),
        )
    logger.info(f"[sensor] Step goal set to {goal}")


def cleanup_old(days: int = 30):
    """Remove sensor data older than N days."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM sensor_data WHERE timestamp < ?", (cutoff,))
