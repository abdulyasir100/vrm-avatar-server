"""CloudStream backend — Python-native scraper engine + yt-dlp downloads.

This module provides:
1. Scraper/extractor registration and initialization
2. Unified search/load/links API using Python scrapers
3. yt-dlp integration for downloading streams
4. ffmpeg integration for media muxing (optional)
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from cli_anything.cloudstream.core.scrapers.base import (
    ScraperRegistry, SearchItem, StreamItem, SubtitleItem,
)
from cli_anything.cloudstream.core.extractors.base import ExtractorRegistry


# ── Initialization ─────────────────────────────────────────────────

_initialized = False


def _init():
    """Register all scrapers and extractors on first use."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    # Register extractors
    from cli_anything.cloudstream.core.extractors.filemoon import FileMoon
    from cli_anything.cloudstream.core.extractors.streamwish import StreamWish
    from cli_anything.cloudstream.core.extractors.mp4upload import Mp4Upload

    ExtractorRegistry.register(FileMoon())
    ExtractorRegistry.register(StreamWish())
    ExtractorRegistry.register(Mp4Upload())

    # Register scrapers
    from cli_anything.cloudstream.core.scrapers.gogoanime import GogoAnime
    from cli_anything.cloudstream.core.scrapers.hianime import HiAnime

    ScraperRegistry.register(GogoAnime())
    ScraperRegistry.register(HiAnime())


# ── Scraper API ────────────────────────────────────────────────────


def scraper_search(query: str, providers: list[str] | None = None) -> dict:
    """Search for content across registered scrapers."""
    _init()

    results = []
    scrapers = ScraperRegistry.all()

    if providers:
        scrapers = [s for s in scrapers if s.name.lower() in [p.lower() for p in providers]]

    for scraper in scrapers:
        try:
            items = scraper.search(query)
            results.extend(item.to_dict() for item in items)
        except Exception as e:
            pass  # Skip failing scrapers silently

    return {
        "status": "ok",
        "query": query,
        "results": results,
        "total": len(results),
        "has_next": False,
    }


def scraper_load(url: str, provider: str | None = None) -> dict:
    """Load content details from a URL."""
    _init()

    # Find matching scraper by URL or provider name
    scraper = None
    if provider:
        scraper = ScraperRegistry.get(provider)

    if not scraper:
        # Try to match by URL
        for s in ScraperRegistry.all():
            if any(mirror in url for mirror in getattr(s, "MIRRORS", [s.base_url])):
                scraper = s
                break

    if not scraper:
        # Use first scraper as fallback
        all_scrapers = ScraperRegistry.all()
        if all_scrapers:
            scraper = all_scrapers[0]

    if not scraper:
        return {"error": "No scraper found for this URL"}

    try:
        content = scraper.load(url)
        if content:
            return content.to_dict()
        return {"error": "Failed to load content"}
    except Exception as e:
        return {"error": str(e)}


def scraper_links(data: str, provider: str | None = None) -> dict:
    """Extract streaming links from episode data."""
    _init()

    # Find matching scraper
    scraper = None
    if provider:
        scraper = ScraperRegistry.get(provider)

    if not scraper:
        for s in ScraperRegistry.all():
            if any(mirror in data for mirror in getattr(s, "MIRRORS", [s.base_url])):
                scraper = s
                break

    if not scraper:
        all_scrapers = ScraperRegistry.all()
        if all_scrapers:
            scraper = all_scrapers[0]

    if not scraper:
        return {"error": "No scraper found for this URL"}

    try:
        streams, subs = scraper.load_links(data)
        return {
            "status": "ok",
            "links": [s.to_dict() for s in streams],
            "subtitles": [s.to_dict() for s in subs],
            "total_links": len(streams),
            "total_subtitles": len(subs),
        }
    except Exception as e:
        return {"error": str(e)}


def scraper_providers() -> dict:
    """List all registered scrapers."""
    _init()
    return {
        "status": "ok",
        "providers": [s.to_dict() for s in ScraperRegistry.all()],
        "total": len(ScraperRegistry.all()),
    }


def scraper_extractors() -> dict:
    """List all registered extractors."""
    _init()
    return {
        "status": "ok",
        "extractors": [e.to_dict() for e in ExtractorRegistry.all()],
        "total": len(ExtractorRegistry.all()),
    }


# ── yt-dlp invocation ──────────────────────────────────────────────


def find_ytdlp() -> str:
    """Find yt-dlp executable. Raises RuntimeError with install instructions."""
    path = shutil.which("yt-dlp")
    if path:
        return path
    raise RuntimeError(
        "yt-dlp is not installed. Install with:\n"
        "  pip install yt-dlp\n"
        "  or: winget install yt-dlp (Windows)\n"
        "  or: brew install yt-dlp (macOS)"
    )


def find_ffmpeg() -> str | None:
    """Find ffmpeg executable. Returns None if not found (optional dep)."""
    return shutil.which("ffmpeg")


def ytdlp_download(url: str, output: str, referer: str | None = None,
                   headers: dict[str, str] | None = None,
                   quality: str | None = None) -> dict:
    """Download a stream using yt-dlp."""
    ytdlp = find_ytdlp()

    cmd = [ytdlp, "-o", output, "--no-warnings"]

    if referer:
        cmd.extend(["--referer", referer])

    if headers:
        for key, value in headers.items():
            cmd.extend(["--add-header", f"{key}:{value}"])

    if quality:
        if quality.isdigit():
            cmd.extend(["-f", f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"])
        else:
            cmd.extend(["-f", quality])

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Download timed out after 10 minutes")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"yt-dlp failed (exit {result.returncode}):\n{stderr}")

    output_path = Path(output)
    if output_path.exists():
        file_size = output_path.stat().st_size
    else:
        candidates = list(output_path.parent.glob(f"{output_path.stem}.*"))
        if candidates:
            output_path = candidates[0]
            file_size = output_path.stat().st_size
        else:
            file_size = 0

    return {
        "status": "ok",
        "output": str(output_path),
        "file_size": file_size,
        "method": "yt-dlp",
    }


def ffmpeg_mux(video: str, subtitle: str, output: str,
               sub_lang: str = "eng") -> dict:
    """Mux video and subtitle files using ffmpeg."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is not installed. Install with:\n"
            "  Windows: winget install ffmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )

    cmd = [
        ffmpeg, "-y",
        "-i", video,
        "-i", subtitle,
        "-c", "copy",
        "-c:s", "mov_text",
        "-metadata:s:s:0", f"language={sub_lang}",
        output,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed:\n{result.stderr[:500]}")

    output_path = Path(output)
    return {
        "status": "ok",
        "output": str(output_path),
        "file_size": output_path.stat().st_size if output_path.exists() else 0,
        "method": "ffmpeg",
    }


# ── Backend status check ──────────────────────────────────────────


def check_backend_status() -> dict:
    """Check availability of all backend components."""
    _init()
    status = {}

    # Scrapers
    scrapers = ScraperRegistry.all()
    status["scrapers"] = {
        "available": len(scrapers) > 0,
        "count": len(scrapers),
        "names": [s.name for s in scrapers],
    }

    # Extractors
    extractors = ExtractorRegistry.all()
    status["extractors"] = {
        "available": len(extractors) > 0,
        "count": len(extractors),
        "names": [e.name for e in extractors],
    }

    # yt-dlp
    try:
        ytdlp = find_ytdlp()
        result = subprocess.run([ytdlp, "--version"], capture_output=True, text=True, timeout=5)
        status["ytdlp"] = {"available": True, "path": ytdlp, "version": result.stdout.strip()}
    except (RuntimeError, Exception) as e:
        status["ytdlp"] = {"available": False, "error": str(e)}

    # ffmpeg
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            result = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=5)
            version_line = result.stdout.split("\n")[0]
            status["ffmpeg"] = {"available": True, "path": ffmpeg, "version": version_line}
        except Exception:
            status["ffmpeg"] = {"available": True, "path": ffmpeg}
    else:
        status["ffmpeg"] = {"available": False, "error": "Not found (optional)"}

    return status
