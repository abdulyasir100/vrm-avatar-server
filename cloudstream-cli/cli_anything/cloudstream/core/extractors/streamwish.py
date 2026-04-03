"""StreamWish extractor — ported from CloudStream's StreamWishExtractor.kt.

Resolves streamwish.to and its many mirror domains to M3U8 streams.
Uses JS unpacking to extract video source.
"""

import re

from cli_anything.cloudstream.core.scrapers.base import StreamItem, SubtitleItem, http_get
from cli_anything.cloudstream.core.extractors.base import ExtractorBase
from cli_anything.cloudstream.core.extractors.js_unpacker import get_and_unpack, is_packed


class StreamWish(ExtractorBase):
    name = "StreamWish"
    main_url = "https://streamwish.to"
    requires_referer = True

    _domains = [
        "streamwish.to", "streamwish.site", "streamwish.com",
        "dwish.pro", "mwish.pro", "embedwish.com", "wishembed.pro",
        "wishfast.top", "sfastwish.com", "strwish.xyz", "strwish.com",
        "awish.pro", "obeywish.com", "cdnwish.com", "asnwish.com",
        "swdyu.com", "wishonly.site", "playerwish.com",
        "streamhls.to", "hlswish.com",
    ]

    def can_handle(self, url: str) -> bool:
        return any(d in url for d in self._domains)

    def extract(self, url: str, referer: str | None = None) -> tuple[list[StreamItem], list[SubtitleItem]]:
        streams = []
        subs = []

        # Normalize URL: /f/ → /e/ (file → embed)
        embed_url = url
        if "/f/" in embed_url:
            video_id = url.split("/f/")[1].split("/")[0].split("?")[0]
            embed_url = f"{self.main_url}/{video_id}"
        elif "/e/" in embed_url:
            video_id = url.split("/e/")[1].split("/")[0].split("?")[0]
            embed_url = f"{self.main_url}/{video_id}"

        headers = {
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "Origin": self.main_url,
        }

        resp = http_get(embed_url, referer=referer or self.main_url, headers=headers)
        html = resp.text

        # Unpack packed JavaScript
        unpacked = get_and_unpack(html)

        # Extract M3U8 URL
        m3u8_match = re.search(r'file:\s*"([^"]*\.m3u8[^"]*)"', unpacked)
        if not m3u8_match:
            m3u8_match = re.search(r'sources:\s*\[\s*\{\s*file:\s*"([^"]+)"', unpacked)

        if m3u8_match:
            m3u8_url = m3u8_match.group(1)
            streams.append(StreamItem(
                url=m3u8_url,
                source=self.name,
                quality=1080,
                type="M3U8",
                referer=embed_url,
            ))

        return streams, subs
