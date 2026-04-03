"""Content loading — load detailed information about movies, shows, and episodes.

Wraps CloudStream's load() provider method, providing structured access
to content details, episode lists, and metadata.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Actor:
    """Actor information."""
    name: str
    image: str | None = None
    role: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Actor":
        if isinstance(data, str):
            return cls(name=data)
        actor_data = data.get("actor", data)
        return cls(
            name=actor_data.get("name", ""),
            image=actor_data.get("image"),
            role=data.get("role", data.get("roleString")),
        )


@dataclass
class Episode:
    """Episode information."""
    data: str
    name: str | None = None
    season: int | None = None
    episode: int | None = None
    poster_url: str | None = None
    description: str | None = None
    date: int | None = None
    run_time: int | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Episode":
        return cls(
            data=data.get("data", ""),
            name=data.get("name"),
            season=data.get("season"),
            episode=data.get("episode"),
            poster_url=data.get("poster_url", data.get("posterUrl")),
            description=data.get("description"),
            date=data.get("date"),
            run_time=data.get("run_time", data.get("runTime")),
        )

    @property
    def display_name(self) -> str:
        """Human-readable episode identifier."""
        parts = []
        if self.season is not None:
            parts.append(f"S{self.season:02d}")
        if self.episode is not None:
            parts.append(f"E{self.episode:02d}")
        label = "".join(parts)
        if self.name:
            return f"{label} - {self.name}" if label else self.name
        return label or "Episode"


@dataclass
class ContentDetails:
    """Full content details from a provider's load() response."""
    name: str
    url: str
    api_name: str
    type: str
    poster_url: str | None = None
    year: int | None = None
    plot: str | None = None
    score: float | None = None
    tags: list[str] = field(default_factory=list)
    duration: int | None = None
    actors: list[Actor] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    coming_soon: bool = False
    show_status: str | None = None
    content_rating: str | None = None
    background_url: str | None = None
    # Movie-specific
    data_url: str | None = None
    # Series-specific
    episodes: list[Episode] = field(default_factory=list)
    # Anime-specific (episodes keyed by dub status)
    episodes_by_dub: dict[str, list[Episode]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "url": self.url,
            "api_name": self.api_name,
            "type": self.type,
        }
        if self.poster_url:
            d["poster_url"] = self.poster_url
        if self.year:
            d["year"] = self.year
        if self.plot:
            d["plot"] = self.plot
        if self.score is not None:
            d["score"] = self.score
        if self.tags:
            d["tags"] = self.tags
        if self.duration:
            d["duration"] = self.duration
        if self.actors:
            d["actors"] = [a.to_dict() for a in self.actors]
        if self.show_status:
            d["show_status"] = self.show_status
        if self.content_rating:
            d["content_rating"] = self.content_rating
        if self.data_url:
            d["data_url"] = self.data_url
        if self.episodes:
            d["episodes"] = [e.to_dict() for e in self.episodes]
        if self.episodes_by_dub:
            d["episodes_by_dub"] = {
                k: [e.to_dict() for e in v]
                for k, v in self.episodes_by_dub.items()
            }
        d["total_episodes"] = self.total_episodes
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ContentDetails":
        actors = [Actor.from_dict(a) for a in data.get("actors", [])]
        episodes = [Episode.from_dict(e) for e in data.get("episodes", [])]
        episodes_by_dub = {}
        for status, eps in data.get("episodes_by_dub", data.get("episodesByDub", {})).items():
            episodes_by_dub[status] = [Episode.from_dict(e) for e in eps]

        return cls(
            name=data.get("name", ""),
            url=data.get("url", ""),
            api_name=data.get("api_name", data.get("apiName", "")),
            type=data.get("type", "Movie"),
            poster_url=data.get("poster_url", data.get("posterUrl")),
            year=data.get("year"),
            plot=data.get("plot"),
            score=data.get("score"),
            tags=data.get("tags", []),
            duration=data.get("duration"),
            actors=actors,
            recommendations=data.get("recommendations", []),
            coming_soon=data.get("coming_soon", data.get("comingSoon", False)),
            show_status=data.get("show_status", data.get("showStatus")),
            content_rating=data.get("content_rating", data.get("contentRating")),
            background_url=data.get("background_url", data.get("backgroundPosterUrl")),
            data_url=data.get("data_url", data.get("dataUrl")),
            episodes=episodes,
            episodes_by_dub=episodes_by_dub,
        )

    @property
    def total_episodes(self) -> int:
        """Total number of episodes across all dub statuses."""
        if self.episodes:
            return len(self.episodes)
        return sum(len(eps) for eps in self.episodes_by_dub.values())

    @property
    def is_movie(self) -> bool:
        return self.type in ("Movie", "AnimeMovie")

    @property
    def is_series(self) -> bool:
        return self.type in ("TvSeries", "Anime", "Cartoon", "AsianDrama", "OVA", "Documentary")

    def get_episode(self, season: int | None = None,
                    episode: int | None = None,
                    index: int | None = None) -> Optional["Episode"]:
        """Get a specific episode by season/episode number or index."""
        all_eps = self.episodes
        if not all_eps and self.episodes_by_dub:
            # Flatten all dub statuses
            for eps in self.episodes_by_dub.values():
                all_eps = eps
                break

        if index is not None and 0 <= index < len(all_eps):
            return all_eps[index]

        for ep in all_eps:
            if season is not None and ep.season != season:
                continue
            if episode is not None and ep.episode != episode:
                continue
            return ep

        return None

    def get_seasons(self) -> list[int]:
        """Get list of unique season numbers."""
        seasons = set()
        for ep in self.episodes:
            if ep.season is not None:
                seasons.add(ep.season)
        return sorted(seasons)


def load_content(url: str) -> ContentDetails:
    """Load full content details from a URL.

    Args:
        url: Content URL from a search result.

    Returns:
        ContentDetails with full metadata and episodes.

    Raises:
        RuntimeError: If backend is unavailable or load fails.
    """
    from cli_anything.cloudstream.utils.cloudstream_backend import scraper_load

    result = scraper_load(url)

    if "error" in result:
        raise RuntimeError(result["error"])

    return ContentDetails.from_dict(result)


def format_content_info(content: ContentDetails) -> dict[str, str]:
    """Format content details as key-value pairs for display."""
    info = {"Title": content.name, "Type": content.type}
    if content.year:
        info["Year"] = str(content.year)
    if content.score is not None:
        info["Score"] = f"{content.score}/10"
    if content.duration:
        info["Duration"] = f"{content.duration} min"
    if content.tags:
        info["Tags"] = ", ".join(content.tags[:5])
    if content.show_status:
        info["Status"] = content.show_status
    if content.content_rating:
        info["Rating"] = content.content_rating
    if content.actors:
        info["Cast"] = ", ".join(a.name for a in content.actors[:5])
    if content.total_episodes > 0:
        info["Episodes"] = str(content.total_episodes)
    return info


def format_episodes_table(content: ContentDetails) -> tuple[list[str], list[list[str]]]:
    """Format episode list as table headers and rows."""
    headers = ["#", "Season", "Episode", "Name", "Duration"]
    rows = []

    episodes = content.episodes
    if not episodes and content.episodes_by_dub:
        for status, eps in content.episodes_by_dub.items():
            episodes = eps
            break

    for i, ep in enumerate(episodes):
        rows.append([
            str(i + 1),
            str(ep.season) if ep.season is not None else "-",
            str(ep.episode) if ep.episode is not None else "-",
            (ep.name or "-")[:30],
            f"{ep.run_time}m" if ep.run_time else "-",
        ])

    return headers, rows
