"""HiAnime scraper — search, load, and extract streaming links.

HiAnime (formerly Aniwatch / Zoro.to) is a popular anime site with
both sub and dub support. Uses internal AJAX endpoints for data.

Technique: HTML scraping + AJAX JSON endpoints + MegaCloud/Rabbitstream extraction.
"""

import json
import re

from cli_anything.cloudstream.core.scrapers.base import (
    ScraperBase, SearchItem, ContentItem, EpisodeItem, StreamItem, SubtitleItem,
    http_get, soup,
)
from cli_anything.cloudstream.core.extractors.base import ExtractorRegistry


class HiAnime(ScraperBase):
    name = "HiAnime"
    base_url = "https://hianime.to"
    supported_types = ["Anime", "AnimeMovie", "OVA"]
    lang = "en"

    MIRRORS = ["hianime.to", "hianime.nz", "hianime.sz", "aniwatch.to"]

    def search(self, query: str) -> list[SearchItem]:
        """Search HiAnime for anime titles."""
        url = f"{self.base_url}/search?keyword={query.replace(' ', '+')}"
        resp = http_get(url)
        doc = soup(resp.text)

        results = []
        for item in doc.select("div.flw-item"):
            link = item.select_one("h3.film-name a")
            img = item.select_one("img.film-poster-img")
            info = item.select_one("div.fd-infor")

            if not link:
                continue

            title = link.get_text(strip=True)
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = f"{self.base_url}{href}"

            poster = None
            if img:
                poster = img.get("data-src") or img.get("src")

            # Extract sub/dub info
            sub_dub = None
            tick_sub = item.select_one("div.tick-sub")
            tick_dub = item.select_one("div.tick-dub")
            if tick_sub and tick_dub:
                sub_dub = "Sub & Dub"
            elif tick_dub:
                sub_dub = "Dub"
            elif tick_sub:
                sub_dub = "Sub"

            results.append(SearchItem(
                title=title,
                url=href,
                provider=self.name,
                type="Anime",
                poster=poster,
                sub_or_dub=sub_dub,
            ))

        return results

    def load(self, url: str) -> ContentItem | None:
        """Load anime details and episode list."""
        resp = http_get(url)
        doc = soup(resp.text)

        # Title
        title_el = doc.select_one("h2.film-name")
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        # Poster
        poster_el = doc.select_one("img.film-poster-img")
        poster = None
        if poster_el:
            poster = poster_el.get("src")

        # Description
        plot_el = doc.select_one("div.film-description div.text")
        plot = plot_el.get_text(strip=True) if plot_el else ""

        # Tags/genres
        tags = []
        for a in doc.select("div.anisc-info a[href*='/genre/']"):
            tags.append(a.get_text(strip=True))

        # Status
        status = None
        for item in doc.select("div.anisc-info div.item"):
            label = item.select_one("span.item-head")
            if label and "status" in label.get_text().lower():
                val = item.select_one("span.name")
                if val:
                    status = val.get_text(strip=True)

        # Get anime ID from URL (e.g., /watch/anime-name-12345)
        anime_id = self._extract_id(url)
        episodes = self._load_episodes(anime_id) if anime_id else []

        return ContentItem(
            title=title,
            url=url,
            provider=self.name,
            type="Anime",
            poster=poster,
            plot=plot,
            tags=tags,
            status=status,
            episodes=episodes,
        )

    def _extract_id(self, url: str) -> str | None:
        """Extract anime ID from URL like /watch/name-12345."""
        match = re.search(r"-(\d+)(?:\?|$)", url)
        if match:
            return match.group(1)
        # Try path-based ID
        match = re.search(r"/watch/([^?]+)", url)
        return match.group(1) if match else None

    def _load_episodes(self, anime_id: str) -> list[EpisodeItem]:
        """Load episode list via AJAX endpoint."""
        ajax_url = f"{self.base_url}/ajax/v2/episode/list/{anime_id}"
        try:
            resp = http_get(ajax_url, referer=self.base_url, headers={
                "X-Requested-With": "XMLHttpRequest",
            })
            data = resp.json()
        except Exception:
            return []

        html = data.get("html", "")
        doc = soup(html)

        episodes = []
        for a in doc.select("a.ssl-item, a.ep-item"):
            href = a.get("href", "")
            ep_num = a.get("data-number") or a.get("data-id")
            ep_title = a.get("title", "")

            if href and not href.startswith("http"):
                href = f"{self.base_url}{href}"

            ep_number = None
            if ep_num:
                try:
                    ep_number = int(ep_num)
                except ValueError:
                    pass

            # data field is the episode URL with query params
            data_id = a.get("data-id", "")

            episodes.append(EpisodeItem(
                data=href or data_id,
                number=ep_number,
                title=ep_title or None,
            ))

        return episodes

    def load_links(self, data: str) -> tuple[list[StreamItem], list[SubtitleItem]]:
        """Extract streaming links from an episode.

        HiAnime uses AJAX to list available servers, then each server
        has an embed URL resolved by MegaCloud/Rabbitstream extractors.
        """
        streams = []
        subs = []

        # Extract episode ID from URL
        ep_id = None
        match = re.search(r"ep=(\d+)", data)
        if match:
            ep_id = match.group(1)
        else:
            match = re.search(r"-(\d+)(?:\?|$)", data)
            if match:
                ep_id = match.group(1)

        if not ep_id:
            # Try loading the page directly for embed URLs
            return self._extract_from_page(data)

        # Get available servers via AJAX
        servers_url = f"{self.base_url}/ajax/v2/episode/servers?episodeId={ep_id}"
        try:
            resp = http_get(servers_url, referer=self.base_url, headers={
                "X-Requested-With": "XMLHttpRequest",
            })
            server_data = resp.json()
        except Exception:
            return streams, subs

        html = server_data.get("html", "")
        doc = soup(html)

        # Each server has a data-id that we can use to get the embed URL
        for server in doc.select("div.server-item"):
            server_id = server.get("data-id", "")
            server_name = server.get_text(strip=True)

            if not server_id:
                continue

            # Get embed URL via AJAX
            source_url = f"{self.base_url}/ajax/v2/episode/sources?id={server_id}"
            try:
                resp = http_get(source_url, referer=self.base_url, headers={
                    "X-Requested-With": "XMLHttpRequest",
                })
                source_data = resp.json()
            except Exception:
                continue

            embed_url = source_data.get("link", "")
            if not embed_url:
                continue

            # Try registered extractors
            extractor = ExtractorRegistry.find(embed_url)
            if extractor:
                try:
                    ext_streams, ext_subs = extractor.extract(embed_url, referer=self.base_url)
                    streams.extend(ext_streams)
                    subs.extend(ext_subs)
                except Exception:
                    pass
            else:
                streams.append(StreamItem(
                    url=embed_url,
                    source=server_name or "Unknown",
                    quality=0,
                    type="M3U8",
                    referer=self.base_url,
                ))

        return streams, subs

    def _extract_from_page(self, url: str) -> tuple[list[StreamItem], list[SubtitleItem]]:
        """Fallback: extract embed URLs directly from the episode page."""
        streams = []
        subs = []

        try:
            resp = http_get(url)
            doc = soup(resp.text)
        except Exception:
            return streams, subs

        for iframe in doc.select("iframe[src]"):
            embed_url = iframe.get("src", "")
            if embed_url.startswith("//"):
                embed_url = "https:" + embed_url

            extractor = ExtractorRegistry.find(embed_url)
            if extractor:
                try:
                    ext_streams, ext_subs = extractor.extract(embed_url, referer=url)
                    streams.extend(ext_streams)
                    subs.extend(ext_subs)
                except Exception:
                    pass

        return streams, subs
