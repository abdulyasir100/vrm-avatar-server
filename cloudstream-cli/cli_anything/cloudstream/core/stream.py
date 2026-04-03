"""Stream extraction — extract playable links from episodes.

Wraps CloudStream's loadLinks() provider method and extractor system
to resolve embed URLs into direct streaming URLs.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


class LinkType:
    """Stream link type constants matching CloudStream's ExtractorLinkType."""
    VIDEO = "VIDEO"
    M3U8 = "M3U8"
    DASH = "DASH"
    TORRENT = "TORRENT"
    MAGNET = "MAGNET"


@dataclass
class StreamLink:
    """A playable stream link extracted from content."""
    source: str
    name: str
    url: str
    referer: str = ""
    quality: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    type: str = "VIDEO"
    is_m3u8: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["quality_label"] = self.quality_label
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "StreamLink":
        link_type = data.get("type", "VIDEO")
        return cls(
            source=data.get("source", ""),
            name=data.get("name", ""),
            url=data.get("url", ""),
            referer=data.get("referer", ""),
            quality=data.get("quality", 0),
            headers=data.get("headers", {}),
            type=link_type,
            is_m3u8=link_type == "M3U8" or data.get("is_m3u8", data.get("isM3u8", False)),
        )

    @property
    def quality_label(self) -> str:
        """Human-readable quality label."""
        if self.quality >= 2160:
            return "4K"
        elif self.quality >= 1080:
            return "1080p"
        elif self.quality >= 720:
            return "720p"
        elif self.quality >= 480:
            return "480p"
        elif self.quality >= 360:
            return "360p"
        elif self.quality > 0:
            return f"{self.quality}p"
        return "Unknown"

    @property
    def all_headers(self) -> dict[str, str]:
        """Combined headers including referer."""
        h = dict(self.headers)
        if self.referer:
            h["Referer"] = self.referer
        return h


@dataclass
class SubtitleTrack:
    """A subtitle track associated with content."""
    lang: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SubtitleTrack":
        return cls(
            lang=data.get("lang", ""),
            url=data.get("url", ""),
            headers=data.get("headers", {}),
        )


@dataclass
class StreamResult:
    """Result of link extraction containing streams and subtitles."""
    links: list[StreamLink] = field(default_factory=list)
    subtitles: list[SubtitleTrack] = field(default_factory=list)
    source_data: str = ""

    def to_dict(self) -> dict:
        return {
            "links": [l.to_dict() for l in self.links],
            "subtitles": [s.to_dict() for s in self.subtitles],
            "total_links": len(self.links),
            "total_subtitles": len(self.subtitles),
            "best_quality": self.best_quality.quality if self.best_quality else 0,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StreamResult":
        links = [StreamLink.from_dict(l) for l in data.get("links", [])]
        subtitles = [SubtitleTrack.from_dict(s) for s in data.get("subtitles", [])]
        return cls(links=links, subtitles=subtitles)

    @property
    def best_quality(self) -> Optional[StreamLink]:
        """Get the highest quality stream link."""
        if not self.links:
            return None
        return max(self.links, key=lambda l: l.quality)

    def filter_by_quality(self, min_quality: int = 0,
                          max_quality: int = 99999) -> list[StreamLink]:
        """Filter links by quality range."""
        return [
            l for l in self.links
            if min_quality <= l.quality <= max_quality
        ]

    def filter_by_type(self, link_type: str) -> list[StreamLink]:
        """Filter links by type (VIDEO, M3U8, DASH, etc.)."""
        return [l for l in self.links if l.type == link_type]

    def get_subtitles_for_lang(self, lang: str) -> list[SubtitleTrack]:
        """Get subtitles for a specific language."""
        lang_lower = lang.lower()
        return [
            s for s in self.subtitles
            if lang_lower in s.lang.lower()
        ]


def extract_links(data: str, provider: str | None = None) -> StreamResult:
    """Extract streaming links from episode data.

    Args:
        data: Episode data string (URL or JSON from Episode.data).
        provider: Optional provider name for context.

    Returns:
        StreamResult with extracted links and subtitles.

    Raises:
        RuntimeError: If backend is unavailable or extraction fails.
    """
    from cli_anything.cloudstream.utils.cloudstream_backend import scraper_links

    result = scraper_links(data, provider)

    if "error" in result:
        raise RuntimeError(result["error"])

    stream_result = StreamResult.from_dict(result)
    stream_result.source_data = data
    return stream_result


def format_links_table(result: StreamResult) -> tuple[list[str], list[list[str]]]:
    """Format stream links as table headers and rows."""
    headers = ["#", "Source", "Quality", "Type", "URL"]
    rows = []

    # Sort by quality descending
    sorted_links = sorted(result.links, key=lambda l: l.quality, reverse=True)

    for i, link in enumerate(sorted_links, 1):
        url_display = link.url[:50] + "..." if len(link.url) > 50 else link.url
        rows.append([
            str(i),
            link.source[:15],
            link.quality_label,
            link.type,
            url_display,
        ])

    return headers, rows


def format_subtitles_table(result: StreamResult) -> tuple[list[str], list[list[str]]]:
    """Format subtitle tracks as table headers and rows."""
    headers = ["#", "Language", "URL"]
    rows = []

    for i, sub in enumerate(result.subtitles, 1):
        url_display = sub.url[:60] + "..." if len(sub.url) > 60 else sub.url
        rows.append([str(i), sub.lang, url_display])

    return headers, rows
