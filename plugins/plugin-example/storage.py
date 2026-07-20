"""Example plugin storage — SQLite template.

The DB file path is set by plugin_loader (stored in data/plugins/<name>/data.db).
This persists across Docker rebuilds via the ./data volume mount.
"""

import logging
import sqlite3
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)
_DB_PATH = None
_lock = Lock()


def init(db_path: str):
    """Initialize SQLite database. Called by plugin_loader at startup."""
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
    logger.info(f"[example] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# Add your CRUD functions here...
