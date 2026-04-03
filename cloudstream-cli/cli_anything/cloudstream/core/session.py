"""Session management — persist state between CLI commands.

Maintains search history, last results, favorites, and provider
configuration across CLI invocations.
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class HistoryEntry:
    """A history entry for a search or load operation."""
    action: str  # "search", "load", "links", "download"
    query: str = ""
    url: str = ""
    provider: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Favorite:
    """A favorited/bookmarked content item."""
    name: str
    url: str
    type: str = "Movie"
    poster_url: str | None = None
    provider: str = ""
    added_at: float = 0.0

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Favorite":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SessionState:
    """Persistent session state."""
    version: str = "1.0.0"
    active_providers: list[str] = field(default_factory=list)
    last_search_query: str = ""
    last_search_results: list[dict] = field(default_factory=list)
    last_load_url: str = ""
    last_load_response: dict = field(default_factory=dict)
    last_links: list[dict] = field(default_factory=list)
    history: list[HistoryEntry] = field(default_factory=list)
    favorites: list[Favorite] = field(default_factory=list)
    settings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "active_providers": self.active_providers,
            "last_search_query": self.last_search_query,
            "last_search_results": self.last_search_results,
            "last_load_url": self.last_load_url,
            "last_load_response": self.last_load_response,
            "last_links": self.last_links,
            "history": [h.to_dict() for h in self.history],
            "favorites": [f.to_dict() for f in self.favorites],
            "settings": self.settings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        history = [HistoryEntry.from_dict(h) for h in data.get("history", [])]
        favorites = [Favorite.from_dict(f) for f in data.get("favorites", [])]
        return cls(
            version=data.get("version", "1.0.0"),
            active_providers=data.get("active_providers", []),
            last_search_query=data.get("last_search_query", ""),
            last_search_results=data.get("last_search_results", []),
            last_load_url=data.get("last_load_url", ""),
            last_load_response=data.get("last_load_response", {}),
            last_links=data.get("last_links", []),
            history=history,
            favorites=favorites,
            settings=data.get("settings", {}),
        )


class Session:
    """Manages persistent session state for the CLI.

    State is stored as a JSON file at ~/.cli-anything-cloudstream/session.json
    """

    MAX_HISTORY = 100

    def __init__(self, session_path: str | None = None):
        if session_path is None:
            session_dir = Path.home() / ".cli-anything-cloudstream"
            session_dir.mkdir(parents=True, exist_ok=True)
            self._path = str(session_dir / "session.json")
        else:
            self._path = session_path

        self._state = self._load()

    def _load(self) -> SessionState:
        """Load session from disk."""
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return SessionState.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                pass
        return SessionState()

    def save(self):
        """Save session to disk."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._state.to_dict(), f, indent=2, ensure_ascii=False)

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def path(self) -> str:
        return self._path

    # ── History ────────────────────────────────────────────────────

    def add_history(self, action: str, query: str = "", url: str = "",
                    provider: str = ""):
        """Add a history entry."""
        entry = HistoryEntry(
            action=action,
            query=query,
            url=url,
            provider=provider,
            timestamp=time.time(),
        )
        self._state.history.append(entry)
        # Trim history
        if len(self._state.history) > self.MAX_HISTORY:
            self._state.history = self._state.history[-self.MAX_HISTORY:]
        self.save()

    def get_history(self, action: str | None = None,
                    limit: int = 20) -> list[HistoryEntry]:
        """Get recent history entries."""
        entries = self._state.history
        if action:
            entries = [e for e in entries if e.action == action]
        return entries[-limit:]

    def clear_history(self):
        """Clear all history."""
        self._state.history.clear()
        self.save()

    # ── Search state ──────────────────────────────────────────────

    def set_search_results(self, query: str, results: list[dict]):
        """Store last search results."""
        self._state.last_search_query = query
        self._state.last_search_results = results
        self.add_history("search", query=query)
        self.save()

    def get_search_result(self, index: int) -> Optional[dict]:
        """Get a search result by index (1-based)."""
        idx = index - 1
        if 0 <= idx < len(self._state.last_search_results):
            return self._state.last_search_results[idx]
        return None

    # ── Load state ────────────────────────────────────────────────

    def set_load_response(self, url: str, response: dict):
        """Store last load response."""
        self._state.last_load_url = url
        self._state.last_load_response = response
        self.add_history("load", url=url)
        self.save()

    # ── Links state ───────────────────────────────────────────────

    def set_links(self, links: list[dict]):
        """Store last extracted links."""
        self._state.last_links = links
        self.save()

    def get_link(self, index: int) -> Optional[dict]:
        """Get a link by index (1-based)."""
        idx = index - 1
        if 0 <= idx < len(self._state.last_links):
            return self._state.last_links[idx]
        return None

    # ── Favorites ─────────────────────────────────────────────────

    def add_favorite(self, name: str, url: str, type: str = "Movie",
                     poster_url: str | None = None, provider: str = ""):
        """Add a favorite."""
        # Check for duplicates
        for f in self._state.favorites:
            if f.url == url:
                return  # Already favorited

        fav = Favorite(
            name=name, url=url, type=type,
            poster_url=poster_url, provider=provider,
            added_at=time.time(),
        )
        self._state.favorites.append(fav)
        self.save()

    def remove_favorite(self, url: str) -> bool:
        """Remove a favorite by URL."""
        before = len(self._state.favorites)
        self._state.favorites = [f for f in self._state.favorites if f.url != url]
        if len(self._state.favorites) < before:
            self.save()
            return True
        return False

    def list_favorites(self) -> list[Favorite]:
        """List all favorites."""
        return self._state.favorites

    # ── Providers ─────────────────────────────────────────────────

    def set_active_providers(self, providers: list[str]):
        """Set the list of active providers."""
        self._state.active_providers = providers
        self.save()

    def get_active_providers(self) -> list[str]:
        """Get the list of active providers."""
        return self._state.active_providers

    # ── Settings ──────────────────────────────────────────────────

    def get_setting(self, key: str, default=None):
        """Get a setting value."""
        return self._state.settings.get(key, default)

    def set_setting(self, key: str, value):
        """Set a setting value."""
        self._state.settings[key] = value
        self.save()

    # ── Info ──────────────────────────────────────────────────────

    def info(self) -> dict:
        """Get session info summary."""
        return {
            "path": self._path,
            "version": self._state.version,
            "active_providers": self._state.active_providers,
            "last_search": self._state.last_search_query or "(none)",
            "search_results": len(self._state.last_search_results),
            "last_load": self._state.last_load_url or "(none)",
            "cached_links": len(self._state.last_links),
            "history_entries": len(self._state.history),
            "favorites": len(self._state.favorites),
        }

    def reset(self):
        """Reset session to defaults."""
        self._state = SessionState()
        self.save()
