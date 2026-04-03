"""Provider management — list, configure, and query content providers.

Providers are the core of CloudStream. Each provider scrapes a specific
website for content (movies, TV series, anime, etc.). This module manages
provider discovery and configuration via the JVM backend.
"""

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class TvType(Enum):
    """Content type enumeration matching CloudStream's TvType."""
    MOVIE = "Movie"
    ANIME_MOVIE = "AnimeMovie"
    TV_SERIES = "TvSeries"
    CARTOON = "Cartoon"
    ANIME = "Anime"
    OVA = "OVA"
    TORRENT = "Torrent"
    DOCUMENTARY = "Documentary"
    ASIAN_DRAMA = "AsianDrama"
    LIVE = "Live"
    NSFW = "NSFW"
    OTHERS = "Others"
    MUSIC = "Music"
    AUDIO_BOOK = "AudioBook"
    CUSTOM_MEDIA = "CustomMedia"
    AUDIO = "Audio"
    PODCAST = "Podcast"


class VPNStatus(Enum):
    """VPN requirement status."""
    NONE = "None"
    MIGHT_BE_NEEDED = "MightBeNeeded"
    TORRENT = "Torrent"


class ProviderType(Enum):
    """Provider type classification."""
    META_PROVIDER = "MetaProvider"
    DIRECT_PROVIDER = "DirectProvider"


@dataclass
class ProviderInfo:
    """Information about a content provider."""
    name: str
    main_url: str
    lang: str = "en"
    has_main_page: bool = False
    has_quick_search: bool = False
    supported_types: list[str] = field(default_factory=list)
    vpn_status: str = "None"
    provider_type: str = "DirectProvider"
    uses_webview: bool = False
    has_download_support: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProviderInfo":
        return cls(
            name=data.get("name", ""),
            main_url=data.get("main_url", data.get("mainUrl", "")),
            lang=data.get("lang", "en"),
            has_main_page=data.get("has_main_page", data.get("hasMainPage", False)),
            has_quick_search=data.get("has_quick_search", data.get("hasQuickSearch", False)),
            supported_types=data.get("supported_types", data.get("supportedTypes", [])),
            vpn_status=data.get("vpn_status", data.get("vpnStatus", "None")),
            provider_type=data.get("provider_type", data.get("providerType", "DirectProvider")),
            uses_webview=data.get("uses_webview", data.get("usesWebView", False)),
            has_download_support=data.get("has_download_support", data.get("hasDownloadSupport", True)),
        )


@dataclass
class ExtractorInfo:
    """Information about a link extractor."""
    name: str
    main_url: str
    requires_referer: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractorInfo":
        return cls(
            name=data.get("name", ""),
            main_url=data.get("main_url", data.get("mainUrl", "")),
            requires_referer=data.get("requires_referer", data.get("requiresReferer", False)),
        )


def list_providers() -> list[ProviderInfo]:
    """List all available content providers from registered scrapers."""
    from cli_anything.cloudstream.utils.cloudstream_backend import scraper_providers

    result = scraper_providers()
    providers = result.get("providers", [])
    return [ProviderInfo.from_dict(p) for p in providers]


def list_extractors() -> list[ExtractorInfo]:
    """List all available link extractors from registered extractors."""
    from cli_anything.cloudstream.utils.cloudstream_backend import scraper_extractors

    result = scraper_extractors()
    extractors = result.get("extractors", [])
    return [ExtractorInfo.from_dict(e) for e in extractors]


def get_provider_info(name: str) -> Optional[ProviderInfo]:
    """Get detailed info about a specific provider."""
    providers = list_providers()
    for p in providers:
        if p.name.lower() == name.lower():
            return p
    return None


def filter_providers(
    lang: str | None = None,
    tv_type: str | None = None,
    provider_type: str | None = None,
) -> list[ProviderInfo]:
    """Filter providers by criteria."""
    providers = list_providers()
    if lang:
        providers = [p for p in providers if p.lang == lang]
    if tv_type:
        providers = [p for p in providers if tv_type in p.supported_types]
    if provider_type:
        providers = [p for p in providers if p.provider_type == provider_type]
    return providers
