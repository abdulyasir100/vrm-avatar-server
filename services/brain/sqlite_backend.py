"""Local brain: one SQLite file with an FTS5 index. The clone-friendly default."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from services.brain.base import ID_BASE, BrainBackend, search_terms

_COLUMNS = ("id", "category", "content", "author", "created_at")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(content, content='facts', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF content ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO facts_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS journal_kind_idx ON journal(kind, created_at);
INSERT INTO sqlite_sequence(name, seq)
    SELECT 'facts', {ID_BASE} WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'facts');
"""


class SqliteBrain(BrainBackend):
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        """A committed-then-closed connection (sqlite3's own `with` commits but never closes)."""
        conn = sqlite3.connect(str(self._path), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _insert(self, category, content, author):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "INSERT INTO facts (category, content, author) VALUES (?, ?, ?)",
                (category, content, author),
            ).lastrowid

    def all(self, category=None):
        where, args = ("WHERE category = ?", (category,)) if category else ("", ())
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM facts {where} ORDER BY created_at, id", args
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query, limit=20):
        terms = search_terms(query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {', '.join('f.' + c for c in _COLUMNS)} FROM facts_fts "
                "JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def update(self, fact_id, content):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "UPDATE facts SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (content, fact_id),
            ).rowcount > 0

    def delete(self, fact_id):
        with self._lock, self._connect() as conn:
            return conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    def count(self):
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    def log(self, kind, content, author):
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO journal (kind, content, author) VALUES (?, ?, ?)", (kind, content, author))
            conn.execute("DELETE FROM journal WHERE kind = ? AND created_at < datetime('now', ?)",
                         (kind, f"-{self.JOURNAL_KEEP_DAYS} days"))

    def recent(self, kind, hours, limit=10):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT content, author, CAST((julianday('now') - julianday(created_at)) * 1440 AS INTEGER) AS age_minutes "
                "FROM journal WHERE kind = ? AND created_at >= datetime('now', ?) ORDER BY created_at DESC, id DESC LIMIT ?",
                (kind, f"-{int(hours * 60)} minutes", limit),
            ).fetchall()
        return [dict(r) for r in rows]
