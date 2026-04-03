"""FileMoon extractor — ported from CloudStream's Filemoon.kt.

Resolves filemoon.sx/to/in embed URLs to M3U8 streams.
Uses JS unpacking to extract the video source URL.
"""

import re

from cli_anything.cloudstream.core.scrapers.base import StreamItem, SubtitleItem, http_get
from cli_anything.cloudstream.core.extractors.base import ExtractorBase
from cli_anything.cloudstream.core.extractors.js_unpacker import get_and_unpack, is_packed


class FileMoon(ExtractorBase):
    name = "FileMoon"
    main_url = "https://filemoon.sx"
    requires_referer = True

    # All known FileMoon mirror domains
    _domains = [
        "filemoon.sx", "filemoon.to", "filemoon.in",
        "filemoon.link", "filemoon.nl",
    ]

    def can_handle(self, url: str) -> bool:
        return any(d in url for d in self._domains)

    def extract(self, url: str, referer: str | None = None) -> tuple[list[StreamItem], list[SubtitleItem]]:
        streams = []
        subs = []

        headers = {
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
        }

        resp = http_get(url, referer=referer or url, headers=headers)
        html = resp.text

        # Try to find iframe src
        iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+)', html)
        if iframe_match:
            iframe_url = iframe_match.group(1)
            resp = http_get(iframe_url, referer=url, headers=headers)
            html = resp.text

        # Find and unpack packed JavaScript
        unpacked = get_and_unpack(html)

        # Extract M3U8 URL from sources
        m3u8_match = re.search(r'sources:\s*\[\s*\{\s*file:\s*"([^"]+)"', unpacked)
        if not m3u8_match:
            m3u8_match = re.search(r'file:\s*"([^"]*\.m3u8[^"]*)"', unpacked)

        if m3u8_match:
            m3u8_url = m3u8_match.group(1)
            streams.append(StreamItem(
                url=m3u8_url,
                source=self.name,
                quality=1080,
                type="M3U8",
                referer=url,
            ))

        # Extract subtitles if present
        sub_matches = re.findall(r'\{[^}]*"file":\s*"([^"]+)"[^}]*"label":\s*"([^"]+)"[^}]*"kind":\s*"captions"', unpacked)
        for sub_url, lang in sub_matches:
            subs.append(SubtitleItem(url=sub_url, lang=lang))

        return streams, subs
