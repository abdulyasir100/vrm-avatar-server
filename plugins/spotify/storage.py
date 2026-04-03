"""Spotify plugin storage — auth tokens and playback history."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
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
            CREATE TABLE IF NOT EXISTS auth (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
    logger.info(f"[spotify] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def set_token(key: str, value: str):
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO auth (key, value) VALUES (?, ?)",
            (key, value),
        )


def get_token(key: str) -> str | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value FROM auth WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_tokens() -> dict:
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT key, value FROM auth").fetchall()
    return {r["key"]: r["value"] for r in rows}
