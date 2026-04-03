"""Wishlist storage — items with prices."""

import logging
import sqlite3
import uuid
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
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL,
                priority INTEGER DEFAULT 0,
                bought INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                bought_at TEXT
            );
        """)
    logger.info(f"[wishlist] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["bought"] = bool(d["bought"])
    return d


def add_item(name: str, price: float | None = None, priority: int = 0) -> dict:
    item_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO items (id, name, price, priority, created_at) VALUES (?, ?, ?, ?, ?)",
            (item_id, name, price, priority, now),
        )
    return {"id": item_id, "name": name, "price": price}


def get_all(include_bought: bool = False) -> list[dict]:
    query = "SELECT * FROM items"
    if not include_bought:
        query += " WHERE bought = 0"
    query += " ORDER BY priority DESC, created_at"
    with _lock, _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_by_id(item_id: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _row_to_dict(row) if row else None


def find_by_name(name: str) -> dict | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE bought = 0 AND LOWER(name) LIKE ?",
            (f"%{name.lower()}%",),
        ).fetchone()
    return _row_to_dict(row) if row else None


def mark_bought(item_id: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE items SET bought = 1, bought_at = ? WHERE id = ?",
            (now, item_id),
        )
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return _row_to_dict(row) if row else None


def remove(item_id: str) -> bool:
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return cursor.rowcount > 0


def get_total() -> float:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(price), 0) as total FROM items WHERE bought = 0 AND price IS NOT NULL"
        ).fetchone()
    return row["total"]
