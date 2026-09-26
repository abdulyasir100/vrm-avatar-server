"""SQLite-backed conversation memory (Tier 2 + Tier 3).

Tier 2: Persistent conversation history — survives server restarts.
Tier 3: Core memories — permanent facts, user preferences, relationship milestones.
        Facts and preferences are about the owner, so they live in the shared brain
        (services/brain) where every character sees them; relationship and event
        memories stay in this character's own DB. This module is the one facade.
"""

import sqlite3
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import config
from services import brain

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None
_lock = Lock()
_session_id: str = ""


def init(db_path: str = config.MEMORY_DB_PATH) -> None:
    """Initialize the memory database and create tables if needed."""
    global _DB_PATH, _session_id

    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    _session_id = uuid.uuid4().hex[:12]

    with _connect() as conn:
        if config.SQLITE_WAL:
            # Persistent per-file, so mood/user_state/dashboard connections inherit it.
            # Readers stop blocking the writer — the background loops share this DB.
            conn.execute("PRAGMA journal_mode=WAL")
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

        # synced=1: this shared-category row now lives in the brain; kept here as a backup only.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(core_memories)")}
        if "synced" not in columns:
            conn.execute("ALTER TABLE core_memories ADD COLUMN synced INTEGER NOT NULL DEFAULT 0")

    logger.info(f"Memory DB initialized at {_DB_PATH} (session: {_session_id})")
    sync_to_brain()


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

_PRIVATE_COLUMNS = "id, category, content, source, created_at"
# Rows still owned by this DB: private categories, plus shared ones the brain hasn't taken yet.
_PRIVATE_LIVE = "synced = 0"

_BRAIN_CACHE_SECONDS = 20  # how stale another character's new fact may be in this prompt
_brain_cache: list[dict] = []
_brain_cache_at = 0.0


def _brain_rows() -> list[dict]:
    """Shared facts, oldest first. Serves the last good copy while the brain is unreachable."""
    global _brain_cache, _brain_cache_at
    if time.time() - _brain_cache_at > _BRAIN_CACHE_SECONDS:
        try:
            rows = brain.get().all()
            _brain_cache = [{**r, "source": r["author"], "shared": True} for r in rows]
        except Exception as e:
            logger.warning(f"[memory] Brain unreachable, using cached shared facts: {e}")
        _brain_cache_at = time.time()
    return _brain_cache


def _brain_changed() -> None:
    global _brain_cache_at
    _brain_cache_at = 0.0


def _add_private(category: str, content: str, source: str | None) -> int:
    normalized = content.lower().strip()
    with _lock, _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM core_memories WHERE category = ? AND LOWER(TRIM(content)) = ? LIMIT 1",
            (category, normalized),
        ).fetchone()
        if existing:
            logger.info(f"Core memory duplicate skipped [{category}]: {content[:60]}...")
            return existing["id"]
        return conn.execute(
            "INSERT INTO core_memories (category, content, source) VALUES (?, ?, ?)",
            (category, content, source),
        ).lastrowid


def add_core_memory(category: str, content: str, source: str | None = None) -> int:
    """Store a core memory. Returns the memory ID.

    Categories: 'fact', 'preference' (shared brain) and 'relationship', 'event' (private).
    Duplicates return the existing id. If the brain is down the fact is parked in the
    private table and moved across by the next sync_to_brain().
    """
    if brain.is_shared_category(category):
        try:
            # "llm"/None = this character learned it in conversation; anything else names the
            # outside writer (e.g. "dashboard"), so nobody is credited with a fact they never heard.
            author = config.CHARACTER_NAME if source in (None, "", "llm") else source
            mem_id = brain.get().add(category, content, author)
            _brain_changed()
            logger.info(f"Shared fact added [{category}]: {content[:60]}...")
            return mem_id
        except Exception as e:
            logger.warning(f"[memory] Brain unreachable, parking fact locally: {e}")
    mem_id = _add_private(category, content, source)
    logger.info(f"Core memory added [{category}]: {content[:60]}...")
    return mem_id


def sync_to_brain() -> int:
    """Move parked shared-category rows into the brain. Returns how many moved.

    Runs at startup: the first run migrates a pre-brain install, later runs pick up
    facts saved while the brain was unreachable. Rows stay behind as synced=1 backups,
    so a fact deleted from the brain is never resurrected from here.
    """
    marks = ",".join("?" * len(brain.SHARED_CATEGORIES))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id, category, content FROM core_memories WHERE {_PRIVATE_LIVE} "
            f"AND category IN ({marks}) ORDER BY created_at, id",
            tuple(brain.SHARED_CATEGORIES),
        ).fetchall()
    if not rows:
        return 0
    moved = 0
    try:
        backend = brain.get()
        for row in rows:
            backend.add(row["category"], row["content"], config.CHARACTER_NAME)
            with _lock, _connect() as conn:
                conn.execute("UPDATE core_memories SET synced = 1 WHERE id = ?", (row["id"],))
            moved += 1
    except Exception as e:
        logger.warning(f"[memory] Brain sync stopped after {moved}/{len(rows)}: {e}")
    if moved:
        _brain_changed()
        logger.info(f"[memory] Moved {moved} shared facts into the brain")
    return moved


# Memories that are daily stat logs (e.g. "step count on 2026-09-11: 4,338 steps").
# They are kept as the record of those stats, but they crowd out real memories in
# recency-based picks, so idle talk skips them.
DAILY_STAT_PATTERN = re.compile(r"\bstep count\b|\b\d[\d,]*\s*steps\b", re.IGNORECASE)


def is_daily_stat_memory(content: str) -> bool:
    return bool(DAILY_STAT_PATTERN.search(content or ""))


def get_recent_core_memories(limit: int = 10, exclude_daily_stats: bool = False) -> list[dict]:
    """Get the most recent N core memories (private + shared), newest first.

    exclude_daily_stats=True drops stat-log memories (see DAILY_STAT_PATTERN) before
    applying the limit, so the result is N memories worth talking about.
    """
    mems = sorted(get_core_memories(), key=lambda m: m["created_at"] or "", reverse=True)
    if exclude_daily_stats:
        mems = [m for m in mems if not is_daily_stat_memory(m["content"])]
    return mems[:limit]


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
    """All core memories this character can see: its own, then the shared facts."""
    where, args = _PRIVATE_LIVE, ()
    if category:
        where, args = f"{_PRIVATE_LIVE} AND category = ?", (category,)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_PRIVATE_COLUMNS} FROM core_memories WHERE {where} ORDER BY created_at", args
        ).fetchall()
    shared = [m for m in _brain_rows() if not category or m["category"] == category]
    return [dict(r) for r in rows] + shared


def update_core_memory(memory_id: int, new_content: str) -> bool:
    """Update a core memory's content by ID."""
    if brain.is_brain_id(memory_id):
        updated = brain.get().update(memory_id, new_content)
        _brain_changed()
        return updated
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "UPDATE core_memories SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_content, memory_id),
        )
        return cursor.rowcount > 0


def delete_core_memory(memory_id: int) -> bool:
    """Delete a core memory by ID."""
    if brain.is_brain_id(memory_id):
        deleted = brain.get().delete(memory_id)
        _brain_changed()
        return deleted
    with _lock, _connect() as conn:
        cursor = conn.execute("DELETE FROM core_memories WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0


def _shared_for_prompt(shared: list[dict], query: str | None) -> list[dict]:
    """Every shared fact while they fit; past BRAIN_PROMPT_MAX, the ones this message
    is about plus the newest — a brain fed by several characters only grows."""
    cap = config.BRAIN_PROMPT_MAX
    if len(shared) <= cap:
        return shared
    keep: dict[int, dict] = {}
    if query:
        try:
            keep = {r["id"]: r for r in brain.get().search(query, limit=cap // 2)}
        except Exception as e:
            logger.warning(f"[memory] Brain search failed: {e}")
    for m in reversed(shared):
        if len(keep) >= cap:
            break
        if not is_daily_stat_memory(m["content"]):
            keep.setdefault(m["id"], m)
    return sorted(keep.values(), key=lambda m: m["id"])


def format_core_memories_for_prompt(query: str | None = None) -> str:
    """Core memories as prompt text, with IDs for update/delete.

    Private memories are the character's own. Shared facts name their author when it
    is someone else: usable knowledge, never a memory this character may claim.
    """
    memories = get_core_memories()
    private = [m for m in memories if not m.get("shared")]
    shared = _shared_for_prompt([m for m in memories if m.get("shared")], query)

    lines: list[str] = []
    if private:
        lines.append("\n## Things I Remember About You")
        lines += [f"- #{m['id']} [{m['category']}] {m['content']}" for m in private]
    if shared:
        lines.append(f"\n## Shared Notes About {config.OWNER_NAME}")
        lines.append("All true about him. Unmarked lines you learned yourself; a line marked (via NAME) "
                     "was learned by NAME — use the fact, but never claim you were the one he told.")
        for m in shared:
            via = "" if m["author"] in ("", config.CHARACTER_NAME) else f" (via {m['author']})"
            lines.append(f"- #{m['id']} [{m['category']}] {m['content']}{via}")
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
    return len(get_core_memories())


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
    brain.journal_log(_SUMMARY_KIND, summary)
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


_SUMMARY_KIND = "summary"
_PEER_SUMMARY_HOURS = 72  # same window as this character's own summaries
_PEER_SUMMARY_LIMIT = 4


def format_summaries_for_prompt() -> str:
    """Recent session summaries for the system prompt: this character's own conversations,
    then what he did with the other characters (from the brain's journal)."""
    lines: list[str] = []
    summaries = get_recent_summaries()
    if summaries:
        lines.append("\n## Recent Conversations")
        lines += [f"- {s['summary']}" for s in summaries]

    elsewhere = brain.journal_from_others(_SUMMARY_KIND, _PEER_SUMMARY_HOURS, _PEER_SUMMARY_LIMIT)
    if elsewhere:
        lines.append(f"\n## What {config.OWNER_NAME} Did With Your Fellow Companions")
        lines.append("You were not there. You know it because they keep shared notes — refer to it that way.")
        lines += [f"- (with {e['author']}, {brain.age_label(e['age_minutes'])}) {e['content']}" for e in elsewhere]
    return "\n".join(lines)


# ─── Memory Search & Delete ──────────────────────────────────────────

def search_and_delete_core_memories(query: str) -> list[dict]:
    """Search core memories by keyword and delete all matches. Returns deleted memories."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            f"SELECT id, category, content FROM core_memories WHERE {_PRIVATE_LIVE} AND content LIKE ?",
            (f"%{query}%",),
        ).fetchall()
        deleted = [dict(r) for r in rows]
        if deleted:
            ids = [r["id"] for r in deleted]
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM core_memories WHERE id IN ({placeholders})", ids)
    try:
        deleted += [{k: r[k] for k in ("id", "category", "content")} for r in brain.get().delete_matching(query)]
        _brain_changed()
    except Exception as e:
        logger.warning(f"[memory] Brain unreachable, shared facts not searched: {e}")
    if deleted:
        logger.info(f"[memory] Deleted {len(deleted)} core memories matching '{query}'")
    return deleted
