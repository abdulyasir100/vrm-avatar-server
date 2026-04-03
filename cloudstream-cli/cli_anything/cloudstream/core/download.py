"""Download management — download streams to local files.

Uses yt-dlp as the download backend for M3U8, DASH, and direct video URLs.
Supports quality selection, subtitle embedding, and progress tracking.
"""

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from cli_anything.cloudstream.core.stream import StreamLink, SubtitleTrack, StreamResult


@dataclass
class DownloadRequest:
    """A download request with all parameters."""
    stream: StreamLink
    output_path: str
    quality: str | None = None
    subtitles: list[SubtitleTrack] = field(default_factory=list)
    embed_subs: bool = False
    sub_lang: str | None = None
    overwrite: bool = False

    def to_dict(self) -> dict:
        return {
            "url": self.stream.url,
            "output_path": self.output_path,
            "quality": self.quality,
            "type": self.stream.type,
            "source": self.stream.source,
            "subtitles": [s.to_dict() for s in self.subtitles],
            "embed_subs": self.embed_subs,
        }


@dataclass
class DownloadResult:
    """Result of a download operation."""
    status: str  # "ok", "error", "skipped"
    output: str = ""
    file_size: int = 0
    method: str = "yt-dlp"
    error: str | None = None
    subtitle_output: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    name = name[:200]  # Limit length
    return name


def build_output_path(content_name: str, episode_label: str | None = None,
                      quality: str = "", output_dir: str = ".",
                      ext: str = "mp4") -> str:
    """Build a structured output file path.

    Args:
        content_name: Title of the content.
        episode_label: Episode label (e.g., "S01E05").
        quality: Quality label.
        output_dir: Output directory.
        ext: File extension.

    Returns:
        Full output file path.
    """
    parts = [sanitize_filename(content_name)]
    if episode_label:
        parts.append(sanitize_filename(episode_label))
    if quality:
        parts.append(quality)

    filename = " - ".join(parts) + f".{ext}"
    return os.path.join(output_dir, filename)


def download_stream(request: DownloadRequest) -> DownloadResult:
    """Download a stream using yt-dlp.

    Args:
        request: DownloadRequest with stream and output parameters.

    Returns:
        DownloadResult with status and file info.
    """
    from cli_anything.cloudstream.utils.cloudstream_backend import (
        ytdlp_download, ffmpeg_mux
    )

    output_path = request.output_path

    # Check if file exists
    if os.path.exists(output_path) and not request.overwrite:
        return DownloadResult(
            status="skipped",
            output=output_path,
            error=f"File already exists: {output_path}",
        )

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    try:
        result = ytdlp_download(
            url=request.stream.url,
            output=output_path,
            referer=request.stream.referer or None,
            headers=request.stream.headers or None,
            quality=request.quality,
        )

        dl_result = DownloadResult(
            status="ok",
            output=result.get("output", output_path),
            file_size=result.get("file_size", 0),
            method=result.get("method", "yt-dlp"),
        )

        # Embed subtitles if requested
        if request.embed_subs and request.subtitles:
            sub = request.subtitles[0]
            if request.sub_lang:
                for s in request.subtitles:
                    if request.sub_lang.lower() in s.lang.lower():
                        sub = s
                        break

            muxed_output = output_path.replace(".mp4", ".muxed.mp4")
            try:
                # Download subtitle first
                import subprocess
                sub_path = output_path + ".srt"
                subprocess.run(
                    ["curl", "-sL", "-o", sub_path, sub.url],
                    timeout=30,
                    check=True,
                )

                mux_result = ffmpeg_mux(
                    video=dl_result.output,
                    subtitle=sub_path,
                    output=muxed_output,
                    sub_lang=sub.lang[:3],
                )
                dl_result.subtitle_output = muxed_output

                # Clean up temp subtitle
                if os.path.exists(sub_path):
                    os.remove(sub_path)
            except Exception:
                pass  # Subtitle embedding is best-effort

        return dl_result

    except RuntimeError as e:
        return DownloadResult(status="error", error=str(e))


def download_best(stream_result: StreamResult, output_path: str,
                  preferred_quality: int = 1080,
                  overwrite: bool = False) -> DownloadResult:
    """Download the best available stream from a StreamResult.

    Selects the stream closest to preferred_quality.

    Args:
        stream_result: StreamResult from link extraction.
        output_path: Output file path.
        preferred_quality: Preferred quality in pixels.
        overwrite: Whether to overwrite existing files.

    Returns:
        DownloadResult with status and file info.
    """
    if not stream_result.links:
        return DownloadResult(status="error", error="No streaming links available")

    # Sort by proximity to preferred quality
    sorted_links = sorted(
        stream_result.links,
        key=lambda l: abs(l.quality - preferred_quality) if l.quality > 0 else 99999,
    )

    best = sorted_links[0]

    request = DownloadRequest(
        stream=best,
        output_path=output_path,
        subtitles=stream_result.subtitles,
        overwrite=overwrite,
    )

    return download_stream(request)


def get_stream_info(link: StreamLink) -> dict:
    """Get stream metadata without downloading.

    Returns dict with format, duration, resolution, etc.
    """
    from cli_anything.cloudstream.utils.cloudstream_backend import ytdlp_info

    return ytdlp_info(link.url, referer=link.referer or None)
