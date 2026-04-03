"""Mp4Upload extractor — ported from CloudStream's Mp4Upload.kt.

Resolves mp4upload.com embed URLs to direct MP4 streams.
"""

import re

from cli_anything.cloudstream.core.scrapers.base import StreamItem, SubtitleItem, http_get
from cli_anything.cloudstream.core.extractors.base import ExtractorBase
from cli_anything.cloudstream.core.extractors.js_unpacker import get_and_unpack


class Mp4Upload(ExtractorBase):
    name = "Mp4Upload"
    main_url = "https://www.mp4upload.com"
    requires_referer = True

    def can_handle(self, url: str) -> bool:
        return "mp4upload.com" in url

    def extract(self, url: str, referer: str | None = None) -> tuple[list[StreamItem], list[SubtitleItem]]:
        streams = []

        # Extract video ID and build embed URL
        id_match = re.search(r"mp4upload\.com/(?:embed-)?([A-Za-z0-9]+)", url)
        if not id_match:
            return streams, []

        video_id = id_match.group(1)
        embed_url = f"{self.main_url}/embed-{video_id}.html"

        resp = http_get(embed_url, referer=referer or self.main_url)
        html = resp.text

        # Unpack JS
        unpacked = get_and_unpack(html)

        # Extract quality from height attribute
        quality = 480
        height_match = re.search(r"height=(\d+)", unpacked)
        if height_match:
            quality = int(height_match.group(1))

        # Extract video URL
        video_match = re.search(r'player\.src\("([^"]+)"', unpacked)
        if not video_match:
            video_match = re.search(r'player\.src\([\w\W]*?src:\s*"([^"]+)"', unpacked)
        if not video_match:
            video_match = re.search(r'src:\s*"(https?://[^"]+\.mp4[^"]*)"', unpacked)

        if video_match:
            video_url = video_match.group(1)
            streams.append(StreamItem(
                url=video_url,
                source=self.name,
                quality=quality,
                type="VIDEO",
                referer=embed_url,
            ))

        return streams, []
