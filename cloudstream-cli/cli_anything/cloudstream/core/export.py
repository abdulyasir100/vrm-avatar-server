"""Export pipeline — end-to-end content discovery to download.

Orchestrates the full CloudStream workflow:
search → load → extract links → download via yt-dlp.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from cli_anything.cloudstream.core.content import ContentDetails, Episode
from cli_anything.cloudstream.core.stream import StreamResult, StreamLink
from cli_anything.cloudstream.core.download import (
    DownloadResult, DownloadRequest, download_stream,
    build_output_path, sanitize_filename,
)


@dataclass
class ExportConfig:
    """Configuration for an export/download operation."""
    output_dir: str = "."
    quality: int = 1080
    format: str = "mp4"
    embed_subtitles: bool = False
    subtitle_lang: str | None = None
    overwrite: bool = False
    filename_template: str = "{name} - {episode} [{quality}]"


@dataclass
class ExportResult:
    """Result of an export operation."""
    status: str
    downloads: list[DownloadResult] = field(default_factory=list)
    total_size: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "downloads": [d.to_dict() for d in self.downloads],
            "total_size": self.total_size,
            "total_files": len([d for d in self.downloads if d.status == "ok"]),
            "errors": self.errors,
        }


def select_best_link(stream_result: StreamResult,
                     preferred_quality: int = 1080) -> Optional[StreamLink]:
    """Select the best streaming link based on quality preference.

    Prefers direct VIDEO links, then M3U8, avoiding TORRENT/MAGNET.
    """
    if not stream_result.links:
        return None

    # Filter out torrent/magnet
    viable = [
        l for l in stream_result.links
        if l.type in ("VIDEO", "M3U8", "DASH")
    ]
    if not viable:
        viable = stream_result.links

    # Sort: prefer closest to desired quality, then prefer VIDEO over M3U8
    type_priority = {"VIDEO": 0, "M3U8": 1, "DASH": 2, "TORRENT": 3, "MAGNET": 4}

    viable.sort(key=lambda l: (
        abs(l.quality - preferred_quality) if l.quality > 0 else 99999,
        type_priority.get(l.type, 5),
    ))

    return viable[0]


def export_movie(content: ContentDetails, stream_result: StreamResult,
                 config: ExportConfig) -> ExportResult:
    """Export/download a movie.

    Args:
        content: Content details from load().
        stream_result: Stream links from loadLinks().
        config: Export configuration.

    Returns:
        ExportResult with download status.
    """
    best = select_best_link(stream_result, config.quality)
    if not best:
        return ExportResult(status="error", errors=["No viable streaming links found"])

    output_path = build_output_path(
        content_name=content.name,
        quality=best.quality_label,
        output_dir=config.output_dir,
        ext=config.format,
    )

    request = DownloadRequest(
        stream=best,
        output_path=output_path,
        quality=str(config.quality),
        subtitles=stream_result.subtitles,
        embed_subs=config.embed_subtitles,
        sub_lang=config.subtitle_lang,
        overwrite=config.overwrite,
    )

    result = download_stream(request)

    export = ExportResult(status=result.status, downloads=[result])
    if result.status == "ok":
        export.total_size = result.file_size
    elif result.error:
        export.errors.append(result.error)

    return export


def export_episode(content: ContentDetails, episode: Episode,
                   stream_result: StreamResult,
                   config: ExportConfig) -> ExportResult:
    """Export/download a single episode.

    Args:
        content: Content details.
        episode: Specific episode to download.
        stream_result: Stream links for this episode.
        config: Export configuration.

    Returns:
        ExportResult with download status.
    """
    best = select_best_link(stream_result, config.quality)
    if not best:
        return ExportResult(status="error", errors=["No viable streaming links found"])

    output_path = build_output_path(
        content_name=content.name,
        episode_label=episode.display_name,
        quality=best.quality_label,
        output_dir=config.output_dir,
        ext=config.format,
    )

    request = DownloadRequest(
        stream=best,
        output_path=output_path,
        quality=str(config.quality),
        subtitles=stream_result.subtitles,
        embed_subs=config.embed_subtitles,
        sub_lang=config.subtitle_lang,
        overwrite=config.overwrite,
    )

    result = download_stream(request)

    export = ExportResult(status=result.status, downloads=[result])
    if result.status == "ok":
        export.total_size = result.file_size
    elif result.error:
        export.errors.append(result.error)

    return export


def build_batch_download_plan(content: ContentDetails,
                              episodes: list[Episode] | None = None,
                              config: ExportConfig | None = None) -> list[dict]:
    """Build a download plan for multiple episodes (dry-run).

    Returns a list of planned downloads without executing them.
    """
    config = config or ExportConfig()
    if episodes is None:
        episodes = content.episodes
        if not episodes and content.episodes_by_dub:
            for eps in content.episodes_by_dub.values():
                episodes = eps
                break

    plan = []
    for ep in episodes:
        output_path = build_output_path(
            content_name=content.name,
            episode_label=ep.display_name,
            quality=f"{config.quality}p",
            output_dir=config.output_dir,
            ext=config.format,
        )
        plan.append({
            "episode": ep.display_name,
            "data": ep.data,
            "output": output_path,
            "exists": os.path.exists(output_path),
        })

    return plan
