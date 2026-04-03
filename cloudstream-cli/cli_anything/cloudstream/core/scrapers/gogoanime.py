"""GogoAnime scraper — search, load, and extract streaming links.

GogoAnime is one of the most popular free anime streaming sites.
Domains change frequently; the base URL is configurable.

Technique: HTML scraping with BeautifulSoup + GogoCDN AES decryption.
"""

import json
import re

from cli_anything.cloudstream.core.scrapers.base import (
    ScraperBase, SearchItem, ContentItem, EpisodeItem, StreamItem, SubtitleItem,
    http_get, soup,
)
from cli_anything.cloudstream.core.extractors.base import ExtractorRegistry


class GogoAnime(ScraperBase):
    name = "GogoAnime"
    base_url = "https://anitaku.to"
    _ajax_url = "https://ajax.gogocdn.net"
    supported_types = ["Anime", "AnimeMovie", "OVA"]
    lang = "en"

    # Known mirror domains — update as needed
    MIRRORS = [
        "anitaku.pe", "anitaku.to", "anitaku.so",
        "gogoanime3.co", "gogoanimehd.to",
    ]

    def search(self, query: str) -> list[SearchItem]:
        """Search GogoAnime for anime titles."""
        url = f"{self.base_url}/search.html?keyword={query.replace(' ', '+')}"
        resp = http_get(url)
        doc = soup(resp.text)

        results = []
        for item in doc.select("ul.items li"):
            link = item.select_one("p.name a")
            img = item.select_one("div.img img")
            released = item.select_one("p.released")

            if not link:
                continue

            title = link.get_text(strip=True)
            href = link.get("href", "")
            if href and not href.startswith("http"):
                href = f"{self.base_url}{href}"

            poster = img.get("src", "") if img else None
            year = None
            if released:
                year_match = re.search(r"(\d{4})", released.get_text())
                if year_match:
                    year = int(year_match.group(1))

            results.append(SearchItem(
                title=title,
                url=href,
                provider=self.name,
                type="Anime",
                poster=poster,
                year=year,
            ))

        return results

    def load(self, url: str) -> ContentItem | None:
        """Load anime details and episode list from GogoAnime."""
        resp = http_get(url)
        doc = soup(resp.text)

        # Title
        title_el = doc.select_one("div.anime_info_body_bg h1")
        title = title_el.get_text(strip=True) if title_el else "Unknown"

        # Poster
        poster_el = doc.select_one("div.anime_info_body_bg img")
        poster = poster_el.get("src") if poster_el else None

        # Info fields
        plot = ""
        tags = []
        status = None
        anime_type = "Anime"

        for p in doc.select("div.anime_info_body_bg p.type"):
            span = p.select_one("span")
            if not span:
                continue
            label = span.get_text(strip=True).lower()
            value = p.get_text(strip=True).replace(span.get_text(), "").strip()

            if "plot" in label or "summary" in label:
                plot = value
            elif "genre" in label:
                for a in p.select("a"):
                    tags.append(a.get_text(strip=True))
            elif "status" in label:
                status = value
            elif "type" in label:
                if "movie" in value.lower():
                    anime_type = "AnimeMovie"

        # Episode list — try direct page first, then AJAX fallback
        episodes = self._load_episodes_from_page(doc)
        if not episodes:
            anime_id = None
            id_el = doc.select_one("input#movie_id")
            if id_el:
                anime_id = id_el.get("value")
            if anime_id:
                episodes = self._load_episodes_ajax(anime_id)

        return ContentItem(
            title=title,
            url=url,
            provider=self.name,
            type=anime_type,
            poster=poster,
            plot=plot,
            tags=tags,
            status=status,
            episodes=episodes,
        )

    def _load_episodes_from_page(self, doc) -> list[EpisodeItem]:
        """Load episode list directly from the page HTML."""
        episodes = []

        for li in doc.select("#episode_related li"):
            a = li.select_one("a")
            if not a:
                continue

            href = a.get("href", "").strip()
            if href and not href.startswith("http"):
                href = f"{self.base_url}{href}"

            ep_text = a.get_text(strip=True)
            ep_num = None
            num_match = re.search(r"(\d+)", ep_text)
            if num_match:
                ep_num = int(num_match.group(1))

            episodes.append(EpisodeItem(
                data=href,
                number=ep_num,
                title=ep_text or None,
            ))

        # Episodes are listed in reverse order on the page
        episodes.reverse()
        return episodes

    def _load_episodes_ajax(self, anime_id: str) -> list[EpisodeItem]:
        """Load episode list via AJAX endpoint (fallback)."""
        ep_url = f"{self._ajax_url}/ajax/load-list-episode?ep_start=0&ep_end=9999&id={anime_id}&default_ep=0&alias="
        try:
            resp = http_get(ep_url)
            doc = soup(resp.text)
        except Exception:
            return []

        return self._load_episodes_from_page(doc)

    def load_links(self, data: str) -> tuple[list[StreamItem], list[SubtitleItem]]:
        """Extract streaming links from an episode page.

        GogoAnime episodes contain multiple embed sources in iframes.
        We extract all embed URLs and pass them to registered extractors.
        """
        streams = []
        subs = []

        resp = http_get(data)
        doc = soup(resp.text)

        # Find all embed server links
        for server in doc.select("div.anime_muti_link ul li a"):
            embed_url = server.get("data-video", "")
            if not embed_url:
                continue
            if embed_url.startswith("//"):
                embed_url = "https:" + embed_url

            server_name = server.get_text(strip=True)

            # Try registered extractors
            extractor = ExtractorRegistry.find(embed_url)
            if extractor:
                try:
                    ext_streams, ext_subs = extractor.extract(embed_url, referer=data)
                    streams.extend(ext_streams)
                    subs.extend(ext_subs)
                except Exception:
                    pass
            else:
                # Store raw embed URL as a fallback stream
                streams.append(StreamItem(
                    url=embed_url,
                    source=server_name or "Unknown",
                    quality=0,
                    type="M3U8",
                    referer=data,
                ))

        return streams, subs
