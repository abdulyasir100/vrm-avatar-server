"""Brain backend contract + the logic every backend shares (dedup, search terms).

A backend only supplies storage primitives; what counts as a duplicate lives here,
so SQLite and Postgres cannot drift apart.
"""

import re
from abc import ABC, abstractmethod

# Shared ids start above this so they never collide with a character's private
# core_memories ids — tools, routers and the Telegram bot keep using plain ints.
ID_BASE = 100_000

_TOKEN = re.compile(r"[a-z0-9]+")
_DUPLICATE_OVERLAP = 0.85  # Jaccard on tokens; identical token sets always count


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").lower()))


def search_terms(text: str, limit: int = 12) -> list[str]:
    """Longest distinct tokens of `text` — the OR-terms a backend searches with."""
    return sorted(tokens(text), key=lambda t: (-len(t), t))[:limit]


def is_duplicate(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= _DUPLICATE_OVERLAP


class BrainBackend(ABC):
    """Durable facts about the owner, shared by every character on this brain.

    Rows: {id, category, content, author, created_at}. `author` is whoever noted it —
    a character may use a shared fact, never claim the memory of learning it.
    """

    @abstractmethod
    def _insert(self, category: str, content: str, author: str) -> int: ...

    @abstractmethod
    def all(self, category: str | None = None) -> list[dict]:
        """Every fact, oldest first."""

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Keyword search (any term), best match first."""

    @abstractmethod
    def update(self, fact_id: int, content: str) -> bool: ...

    @abstractmethod
    def delete(self, fact_id: int) -> bool: ...

    @abstractmethod
    def count(self) -> int: ...

    # The journal: dated notes that age out (session summaries, the owner's current
    # state, later the office's activity log) — what is going on, where facts are what is true.
    JOURNAL_KEEP_DAYS = 30

    @abstractmethod
    def log(self, kind: str, content: str, author: str) -> None:
        """Append a journal entry and drop entries of that kind past JOURNAL_KEEP_DAYS."""

    @abstractmethod
    def recent(self, kind: str, hours: float, limit: int = 10) -> list[dict]:
        """Entries of `kind` from the last `hours`, newest first: {content, author, age_minutes}."""

    def add(self, category: str, content: str, author: str) -> int:
        """Store a fact unless an equivalent one exists; returns the (existing) id.

        Two harnesses hear the same things, so the same fact arrives twice in
        different words — without this one fact becomes forty near-copies.
        """
        for row in self.search(content, limit=8):
            if row["category"] == category and is_duplicate(row["content"], content):
                return row["id"]
        return self._insert(category, content, author)

    def delete_matching(self, query: str) -> list[dict]:
        needle = query.lower()
        hits = [r for r in self.all() if needle in r["content"].lower()]
        return [r for r in hits if self.delete(r["id"])]
