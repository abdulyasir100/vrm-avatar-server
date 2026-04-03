"""Money plugin — SQLite storage for transactions and budget."""

import logging
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)
_DB_PATH = None
_lock = Lock()
WIB = timezone(timedelta(hours=7))


def init(db_path: str):
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                amount INTEGER NOT NULL CHECK(amount > 0),
                description TEXT DEFAULT '',
                category TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        # Default budget if not set
        row = conn.execute("SELECT value FROM settings WHERE key = 'monthly_budget'").fetchone()
        if not row:
            conn.execute("INSERT INTO settings (key, value) VALUES ('monthly_budget', '0')")
            conn.execute("INSERT INTO settings (key, value) VALUES ('currency', 'IDR')")
    logger.info(f"[money] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _current_month_wib() -> str:
    return datetime.now(WIB).strftime("%Y-%m")


def create_transaction(tx_type: str, amount: int, description: str = "", category: str = "") -> dict:
    tx_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO transactions (id, type, amount, description, category, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tx_id, tx_type, amount, description, category, now),
        )
    return {"id": tx_id, "type": tx_type, "amount": amount, "description": description, "category": category}


def get_balance() -> dict:
    month = _current_month_wib()
    with _lock, _connect() as conn:
        # All-time
        total_income = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'income'").fetchone()[0]
        total_expense = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'expense'").fetchone()[0]
        # Current month (WIB)
        monthly_income = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'income' AND substr(created_at, 1, 7) >= ?",
            (month,)
        ).fetchone()[0]
        monthly_expense = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'expense' AND substr(created_at, 1, 7) >= ?",
            (month,)
        ).fetchone()[0]
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        currency = conn.execute("SELECT value FROM settings WHERE key = 'currency'").fetchone()

    return {
        "balance": total_income - total_expense,
        "total_income": total_income,
        "total_expense": total_expense,
        "monthly_income": monthly_income,
        "monthly_expense": monthly_expense,
        "transaction_count": count,
        "currency": currency["value"] if currency else "IDR",
    }


def get_spending_status() -> dict:
    month = _current_month_wib()
    with _lock, _connect() as conn:
        monthly_expense = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'expense' AND substr(created_at, 1, 7) >= ?",
            (month,)
        ).fetchone()[0]
        budget_row = conn.execute("SELECT value FROM settings WHERE key = 'monthly_budget'").fetchone()
        currency_row = conn.execute("SELECT value FROM settings WHERE key = 'currency'").fetchone()

    budget = int(budget_row["value"]) if budget_row else 0
    currency = currency_row["value"] if currency_row else "IDR"

    if budget > 0:
        spent_percent = round((monthly_expense / budget) * 100, 1)
    else:
        spent_percent = 0.0

    if spent_percent >= 80:
        threshold = "critical"
    elif spent_percent >= 50:
        threshold = "warning"
    else:
        threshold = "ok"

    return {
        "monthly_budget": budget,
        "monthly_expense": monthly_expense,
        "spent_percent": spent_percent,
        "threshold": threshold,
        "currency": currency,
    }


def set_budget(amount: int):
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('monthly_budget', ?)", (str(amount),))


def get_transactions(tx_type: str | None = None, limit: int = 10) -> list[dict]:
    query = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if tx_type:
        query += " AND type = ?"
        params.append(tx_type)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def delete_transaction(tx_id: str) -> bool:
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    return cursor.rowcount > 0
