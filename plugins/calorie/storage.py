"""Calorie plugin — SQLite storage for meals and nutrition."""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)
_DB_PATH = None
_lock = Lock()
_foods_db: dict = {}
WIB = timezone(timedelta(hours=7))
DEFAULT_TARGET = 2000


def init(db_path: str):
    global _DB_PATH, _foods_db
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load local food database
    foods_path = Path(__file__).parent / "foods.json"
    if foods_path.exists():
        _foods_db = json.loads(foods_path.read_text(encoding="utf-8"))
        logger.info(f"[calorie] Loaded {len(_foods_db)} foods from local DB")

    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meals (
                id TEXT PRIMARY KEY,
                meal_type TEXT NOT NULL,
                food_name TEXT NOT NULL,
                calories INTEGER DEFAULT 0,
                protein_g REAL DEFAULT 0.0,
                carbs_g REAL DEFAULT 0.0,
                fat_g REAL DEFAULT 0.0,
                portion TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        row = conn.execute("SELECT value FROM settings WHERE key = 'daily_target'").fetchone()
        if not row:
            conn.execute("INSERT INTO settings (key, value) VALUES ('daily_target', ?)", (str(DEFAULT_TARGET),))
    logger.info(f"[calorie] Storage initialized at {_DB_PATH}")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _today_wib() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d")


def lookup_food(name: str) -> dict | None:
    """Look up food in local Indonesian foods database."""
    name_lower = name.lower().strip()
    # Exact match
    if name_lower in _foods_db:
        return {**_foods_db[name_lower], "food_name": name, "source": "local"}
    # Partial match (3+ chars)
    if len(name_lower) >= 3:
        for key, val in _foods_db.items():
            if name_lower in key or key in name_lower:
                return {**val, "food_name": name, "source": "local"}
    return None


def create_meal(meal_type: str, food_name: str, calories: int = 0,
                protein_g: float = 0.0, carbs_g: float = 0.0, fat_g: float = 0.0,
                portion: str = "") -> dict:
    meal_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as conn:
        conn.execute(
            """INSERT INTO meals (id, meal_type, food_name, calories, protein_g, carbs_g, fat_g, portion, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (meal_id, meal_type, food_name, calories, protein_g, carbs_g, fat_g, portion, now),
        )
    return {"id": meal_id, "food_name": food_name, "calories": calories}


def get_today_meals() -> list[dict]:
    today = _today_wib()
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM meals WHERE date(created_at, '+7 hours') = ? ORDER BY created_at",
            (today,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_today_stats() -> dict:
    meals = get_today_meals()
    total_cal = sum(m["calories"] for m in meals)
    total_protein = sum(m["protein_g"] for m in meals)
    total_carbs = sum(m["carbs_g"] for m in meals)
    total_fat = sum(m["fat_g"] for m in meals)
    target = get_daily_target()
    return {
        "total_calories": total_cal,
        "total_protein": round(total_protein, 1),
        "total_carbs": round(total_carbs, 1),
        "total_fat": round(total_fat, 1),
        "meal_count": len(meals),
        "daily_target": target,
        "remaining": target - total_cal,
    }


def get_calorie_status() -> dict:
    stats = get_today_stats()
    total = stats["total_calories"]
    target = stats["daily_target"]
    eaten_pct = round((total / target) * 100, 1) if target > 0 else 0.0
    if eaten_pct >= 100:
        threshold = "over"
    elif eaten_pct >= 80:
        threshold = "warning"
    else:
        threshold = "ok"
    return {
        "daily_target": target,
        "total_eaten": total,
        "remaining": stats["remaining"],
        "eaten_percent": eaten_pct,
        "threshold": threshold,
    }


def get_daily_target() -> int:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'daily_target'").fetchone()
    return int(row["value"]) if row else DEFAULT_TARGET


def set_daily_target(target: int):
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_target', ?)", (str(target),))


def delete_meal(meal_id: str) -> bool:
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
    return cursor.rowcount > 0
