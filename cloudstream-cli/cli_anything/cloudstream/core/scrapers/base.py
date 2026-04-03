"""Base scraper class and registry — all providers implement this interface.

Mirrors CloudStream's MainAPI pattern: search() → load() → load_links().
"""

import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Shared session for connection pooling
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})


def http_get(url: str, referer: str | None = None,
             headers: dict | None = None, timeout: int = 15) -> requests.Response:
    """GET request with shared session and defaults."""
    h = dict(headers or {})
    if referer:
        h["Referer"] = referer
    return _session.get(url, headers=h, timeout=timeout)


def http_post(url: str, data: dict | None = None, json_data: dict | None = None,
              referer: str | None = None, headers: dict | None = None,
              timeout: int = 15) -> requests.Response:
    """POST request with shared session and defaults."""
    h = dict(headers or {})
    if referer:
        h["Referer"] = referer
    return _session.post(url, data=data, json=json_data, headers=h, timeout=timeout)


def soup(html: str) -> BeautifulSoup:
    """Parse HTML with BeautifulSoup."""
    return BeautifulSoup(html, "html.parser")


@dataclass
class SearchItem:
    """A search result from a scraper."""
    title: str
    url: str
    provider: str
    type: str = "Anime"
    poster: str | None = None
    year: int | None = None
    sub_or_dub: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "name": self.title,
            "url": self.url,
            "api_name": self.provider,
            "type": self.type,
            "poster_url": self.poster,
            "year": self.year,
            "dub_status": [self.sub_or_dub] if self.sub_or_dub else None,
        }.items() if v is not None}


@dataclass
class EpisodeItem:
    """An episode from a scraper."""
    data: str  # URL or ID passed to load_links()
    number: int | None = None
    title: str | None = None
    season: int | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "data": self.data,
            "episode": self.number,
            "name": self.title,
            "season": self.season,
        }.items() if v is not None}


@dataclass
class ContentItem:
    """Loaded content details from a scraper."""
    title: str
    url: str
    provider: str
    type: str = "Anime"
    poster: str | None = None
    plot: str | None = None
    year: int | None = None
    tags: list[str] = field(default_factory=list)
    status: str | None = None
    episodes: list[EpisodeItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "name": self.title,
            "url": self.url,
            "api_name": self.provider,
            "type": self.type,
            "episodes": [e.to_dict() for e in self.episodes],
            "total_episodes": len(self.episodes),
        }
        if self.poster:
            d["poster_url"] = self.poster
        if self.plot:
            d["plot"] = self.plot
        if self.year:
            d["year"] = self.year
        if self.tags:
            d["tags"] = self.tags
        if self.status:
            d["show_status"] = self.status
        return d


@dataclass
class StreamItem:
    """An extracted stream link."""
    url: str
    source: str
    quality: int = 0
    type: str = "M3U8"  # M3U8, VIDEO, DASH
    referer: str = ""
    headers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "source": self.source,
            "name": f"{self.source} - {self.quality}p" if self.quality else self.source,
            "quality": self.quality,
            "type": self.type,
            "referer": self.referer,
            "headers": self.headers,
        }


@dataclass
class SubtitleItem:
    """A subtitle track."""
    url: str
    lang: str
    headers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"url": self.url, "lang": self.lang, "headers": self.headers}


class ScraperBase:
    """Base class for all site scrapers. Override search/load/load_links."""

    name: str = "Unknown"
    base_url: str = ""
    supported_types: list[str] = ["Anime"]
    lang: str = "en"

    def search(self, query: str) -> list[SearchItem]:
        """Search for content. Override in subclass."""
        raise NotImplementedError

    def load(self, url: str) -> ContentItem | None:
        """Load content details + episode list. Override in subclass."""
        raise NotImplementedError

    def load_links(self, data: str) -> tuple[list[StreamItem], list[SubtitleItem]]:
        """Extract stream links from episode data. Override in subclass."""
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "main_url": self.base_url,
            "lang": self.lang,
            "supported_types": self.supported_types,
            "provider_type": "DirectProvider",
        }


class ScraperRegistry:
    """Registry of available scrapers."""

    _scrapers: dict[str, ScraperBase] = {}

    @classmethod
    def register(cls, scraper: ScraperBase):
        cls._scrapers[scraper.name.lower()] = scraper

    @classmethod
    def get(cls, name: str) -> ScraperBase | None:
        return cls._scrapers.get(name.lower())

    @classmethod
    def all(cls) -> list[ScraperBase]:
        return list(cls._scrapers.values())

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._scrapers.keys())

    @classmethod
    def search_all(cls, query: str) -> list[SearchItem]:
        """Search across all registered scrapers."""
        results = []
        for scraper in cls._scrapers.values():
            try:
                results.extend(scraper.search(query))
            except Exception:
                pass
        return results
