"""SQLite-backed conversation memory (Tier 2 + Tier 3).

Tier 2: Persistent conversation history — survives server restarts.
Tier 3: Core memories — permanent facts, user preferences, relationship milestones.
"""

import sqlite3
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None
_lock = Lock()
_session_id: str = ""


def init(db_path: str = "data/memory.db") -> None:
    """Initialize the memory database and create tables if needed."""
    global _DB_PATH, _session_id

    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    _session_id = uuid.uuid4().hex[:12]

    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                emotion TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS core_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS session_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                message_count INTEGER,
                start_time DATETIME,
                end_time DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_conv_timestamp ON conversations(timestamp);
            CREATE INDEX IF NOT EXISTS idx_core_category ON core_memories(category);
        """)

    logger.info(f"Memory DB initialized at {_DB_PATH} (session: {_session_id})")


def _connect() -> sqlite3.Connection:
    """Create a new connection (thread-safe, one per call)."""
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Tier 2: Conversation History ───────────────────────────────────────

def add_message(role: str, content: str, emotion: str | None = None) -> None:
    """Store a message in persistent conversation history."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (session_id, role, content, emotion) VALUES (?, ?, ?, ?)",
            (_session_id, role, content, emotion),
        )
    if role == "user":
        try:
            from services import user_state
            user_state.mark_dirty()
        except Exception:
            pass


def get_recent_messages(hours: int = 24, soft_cap: int = 100) -> list[dict]:
    """Get messages from the last N hours, soft-capped at a max count.

    Returns list of {"role": str, "content": str} dicts (OpenAI format).
    If the time window has more than soft_cap messages, only the most recent ones are kept.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM conversations "
            "WHERE timestamp > ? ORDER BY id DESC LIMIT ?",
            (cutoff, soft_cap),
        ).fetchall()

    # Reverse to chronological order
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def get_session_messages(session_id: str | None = None) -> list[dict]:
    """Get all messages from a specific session."""
    sid = session_id or _session_id
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, emotion, timestamp FROM conversations "
            "WHERE session_id = ? ORDER BY id",
            (sid,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_sessions(days: int = 7) -> list[dict]:
    """Get summary of recent sessions (for context/debugging)."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT session_id, MIN(timestamp) as started, MAX(timestamp) as ended, "
            "COUNT(*) as message_count "
            "FROM conversations WHERE timestamp > ? "
            "GROUP BY session_id ORDER BY started DESC",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_message_count() -> int:
    """Total messages stored."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM conversations").fetchone()
    return row["cnt"]


def clear_conversation_history() -> None:
    """Delete all conversation messages (keeps core memories)."""
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM conversations")
        conn.commit()
    logger.info("[memory] Conversation history cleared")


# ─── Tier 3: Core Memories ──────────────────────────────────────────────

def add_core_memory(category: str, content: str, source: str | None = None) -> int:
    """Store a core memory. Returns the memory ID.

    Categories: 'fact', 'preference', 'relationship', 'event'
    Deduplicated case-insensitive + whitespace-normalized against existing entries
    in the same category — returns existing id if duplicate found.
    """
    normalized = content.lower().strip()
    with _lock, _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM core_memories WHERE category = ? AND LOWER(TRIM(content)) = ? LIMIT 1",
            (category, normalized),
        ).fetchone()
        if existing:
            logger.info(f"Core memory duplicate skipped [{category}]: {content[:60]}...")
            return existing["id"]
        cursor = conn.execute(
            "INSERT INTO core_memories (category, content, source) VALUES (?, ?, ?)",
            (category, content, source),
        )
        mem_id = cursor.lastrowid
    logger.info(f"Core memory added [{category}]: {content[:60]}...")
    return mem_id


def get_recent_core_memories(limit: int = 10) -> list[dict]:
    """Get the most recent N core memories, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, category, content, source, created_at FROM core_memories "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_assistant_messages(limit: int = 10) -> list[dict]:
    """Get the last N assistant utterances across all sessions, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT content, emotion, timestamp FROM conversations "
            "WHERE role = 'assistant' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def format_recent_assistant_for_prompt(limit: int = 10) -> str:
    """Format recent assistant utterances as a 'don't repeat these' block."""
    msgs = get_recent_assistant_messages(limit)
    if not msgs:
        return ""
    lines = ["\n## Recently You Said (don't repeat these topics or phrasings — vary your angle)"]
    for m in msgs:
        text = m["content"]
        if len(text) > 140:
            text = text[:137] + "..."
        lines.append(f"- {text}")
    return "\n".join(lines) + "\n"


def get_last_user_message_timestamp() -> float | None:
    """Return unix timestamp of the most recent user message, or None if no messages."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT timestamp FROM conversations WHERE role = 'user' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row or not row["timestamp"]:
        return None
    try:
        # SQLite CURRENT_TIMESTAMP stores as 'YYYY-MM-DD HH:MM:SS' (UTC naive)
        dt = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def get_core_memories(category: str | None = None) -> list[dict]:
    """Get all core memories, optionally filtered by category."""
    with _connect() as conn:
        if category:
            rows = conn.execute(
                "SELECT id, category, content, source, created_at FROM core_memories "
                "WHERE category = ? ORDER BY created_at",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, category, content, source, created_at FROM core_memories "
                "ORDER BY created_at",
            ).fetchall()
    return [dict(r) for r in rows]


def update_core_memory(memory_id: int, new_content: str) -> bool:
    """Update a core memory's content by ID."""
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "UPDATE core_memories SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_content, memory_id),
        )
        return cursor.rowcount > 0


def delete_core_memory(memory_id: int) -> bool:
    """Delete a core memory by ID."""
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM core_memories WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0


def format_core_memories_for_prompt() -> str:
    """Format core memories as a text block for the system prompt, with IDs for update/delete."""
    memories = get_core_memories()
    if not memories:
        return ""

    lines = ["\n## Things I Remember About You"]
    for m in memories:
        lines.append(f"- #{m['id']} [{m['category']}] {m['content']}")
    return "\n".join(lines)

def cleanup_old_conversations(keep_days: int = 30) -> int:
    """Delete conversations older than keep_days. Returns rows deleted."""
    cutoff = (datetime.utcnow() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE timestamp < ?", (cutoff,)
        )
        deleted = cursor.rowcount
    if deleted:
        logger.info(f"Cleaned up {deleted} old conversation messages")
    return deleted


def get_core_memory_count() -> int:
    """Total core memories stored."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM core_memories").fetchone()
    return row["cnt"]


def get_current_session_id() -> str:
    """Return the current session ID."""
    return _session_id


# ─── Session Summaries (Tier 2) ──────────────────────────────────────

def add_session_summary(summary: str, message_count: int,
                        start_time: str, end_time: str) -> int:
    """Store a session summary. Returns the summary ID."""
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO session_summaries (summary, message_count, start_time, end_time) "
            "VALUES (?, ?, ?, ?)",
            (summary, message_count, start_time, end_time),
        )
        sid = cursor.lastrowid
    logger.info(f"[memory] Session summary added ({message_count} msgs): {summary[:60]}...")
    return sid


def get_recent_summaries(days: int = 3, limit: int = 5) -> list[dict]:
    """Get recent session summaries."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, summary, message_count, start_time, end_time, created_at "
            "FROM session_summaries WHERE created_at > ? "
            "ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_unsummarized_messages() -> list[dict]:
    """Get conversation messages that haven't been covered by any session summary."""
    with _connect() as conn:
        # Find the latest summary's end_time
        row = conn.execute(
            "SELECT end_time FROM session_summaries ORDER BY end_time DESC LIMIT 1"
        ).fetchone()
        if row and row["end_time"]:
            cutoff = row["end_time"]
        else:
            # No summaries yet — get messages from last 24h
            cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

        rows = conn.execute(
            "SELECT role, content, emotion, timestamp FROM conversations "
            "WHERE timestamp > ? ORDER BY id",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def format_summaries_for_prompt() -> str:
    """Format recent session summaries for injection into system prompt."""
    summaries = get_recent_summaries()
    if not summaries:
        return ""

    lines = ["\n## Recent Conversations"]
    for s in summaries:
        lines.append(f"- {s['summary']}")
    return "\n".join(lines)


# ─── Memory Search & Delete ──────────────────────────────────────────

def search_and_delete_core_memories(query: str) -> list[dict]:
    """Search core memories by keyword and delete all matches. Returns deleted memories."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT id, category, content FROM core_memories WHERE content LIKE ?",
            (f"%{query}%",),
        ).fetchall()
        deleted = [dict(r) for r in rows]
        if deleted:
            ids = [r["id"] for r in deleted]
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM core_memories WHERE id IN ({placeholders})", ids)
            logger.info(f"[memory] Deleted {len(deleted)} core memories matching '{query}'")
    return deleted
