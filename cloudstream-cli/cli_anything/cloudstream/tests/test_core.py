"""Unit tests for CloudStream CLI harness core modules.

All tests use synthetic data — no external dependencies required.
"""

import json
import os
import tempfile

import pytest


# ── provider.py tests ─────────────────────────────────────────────


class TestProviderInfo:
    def test_provider_info_creation(self):
        from cli_anything.cloudstream.core.provider import ProviderInfo

        data = {
            "name": "TestProvider",
            "mainUrl": "https://example.com",
            "lang": "en",
            "hasMainPage": True,
            "hasQuickSearch": False,
            "supportedTypes": ["Movie", "TvSeries"],
            "vpnStatus": "None",
            "providerType": "DirectProvider",
            "usesWebView": False,
            "hasDownloadSupport": True,
        }
        info = ProviderInfo.from_dict(data)
        assert info.name == "TestProvider"
        assert info.main_url == "https://example.com"
        assert info.lang == "en"
        assert info.has_main_page is True
        assert "Movie" in info.supported_types

    def test_provider_info_to_dict(self):
        from cli_anything.cloudstream.core.provider import ProviderInfo

        info = ProviderInfo(
            name="Test",
            main_url="https://test.com",
            lang="ja",
            supported_types=["Anime"],
        )
        d = info.to_dict()
        assert d["name"] == "Test"
        assert d["lang"] == "ja"
        assert "Anime" in d["supported_types"]

    def test_extractor_info_creation(self):
        from cli_anything.cloudstream.core.provider import ExtractorInfo

        data = {"name": "FileMoon", "mainUrl": "https://filemoon.sx", "requiresReferer": True}
        info = ExtractorInfo.from_dict(data)
        assert info.name == "FileMoon"
        assert info.requires_referer is True

    def test_list_providers(self):
        from cli_anything.cloudstream.core.provider import list_providers

        providers = list_providers()
        assert len(providers) >= 2
        names = [p.name for p in providers]
        assert "GogoAnime" in names
        assert "HiAnime" in names

    def test_list_extractors(self):
        from cli_anything.cloudstream.core.provider import list_extractors

        extractors = list_extractors()
        assert len(extractors) >= 3
        names = [e.name for e in extractors]
        assert "FileMoon" in names
        assert "StreamWish" in names

    def test_filter_providers_by_lang(self):
        from cli_anything.cloudstream.core.provider import ProviderInfo, filter_providers

        # Uses fallback providers which are all "en"
        providers = filter_providers(lang="en")
        assert all(p.lang == "en" for p in providers)

    def test_filter_providers_by_type(self):
        from cli_anything.cloudstream.core.provider import filter_providers

        providers = filter_providers(tv_type="Movie")
        for p in providers:
            assert "Movie" in p.supported_types

    def test_tv_type_enum(self):
        from cli_anything.cloudstream.core.provider import TvType

        assert TvType.MOVIE.value == "Movie"
        assert TvType.ANIME.value == "Anime"
        assert TvType.LIVE.value == "Live"
        assert TvType.TORRENT.value == "Torrent"


# ── search.py tests ───────────────────────────────────────────────


class TestSearch:
    def test_search_result_from_dict(self):
        from cli_anything.cloudstream.core.search import SearchResult

        data = {
            "name": "Inception",
            "url": "https://example.com/inception",
            "apiName": "TMDB",
            "type": "Movie",
            "year": 2010,
            "quality": "HD",
        }
        r = SearchResult.from_dict(data)
        assert r.name == "Inception"
        assert r.api_name == "TMDB"
        assert r.year == 2010

    def test_search_result_to_dict_excludes_none(self):
        from cli_anything.cloudstream.core.search import SearchResult

        r = SearchResult(name="Test", url="http://test.com", api_name="P1")
        d = r.to_dict()
        assert "name" in d
        assert "poster_url" not in d  # None fields excluded

    def test_search_results_collection(self):
        from cli_anything.cloudstream.core.search import SearchResult, SearchResults

        results = SearchResults(
            query="test",
            results=[
                SearchResult(name="A", url="http://a.com", api_name="P1"),
                SearchResult(name="B", url="http://b.com", api_name="P2"),
            ],
            has_next=True,
        )
        d = results.to_dict()
        assert d["query"] == "test"
        assert len(d["results"]) == 2
        assert d["has_next"] is True
        assert d["total"] == 2

    def test_format_results_table(self):
        from cli_anything.cloudstream.core.search import (
            SearchResult, SearchResults, format_results_table,
        )

        results = SearchResults(
            query="inception",
            results=[
                SearchResult(name="Inception", url="http://x.com", api_name="TMDB",
                             type="Movie", year=2010, quality="HD"),
            ],
        )
        headers, rows = format_results_table(results)
        assert "Name" in headers
        assert len(rows) == 1
        assert "Inception" in rows[0][1]

    def test_search_result_with_all_fields(self):
        from cli_anything.cloudstream.core.search import SearchResult

        r = SearchResult(
            name="Naruto",
            url="http://naruto.com",
            api_name="GogoAnime",
            type="Anime",
            poster_url="http://poster.jpg",
            year=2002,
            quality="HD",
            score=8.5,
            dub_status=["Dubbed", "Subbed"],
        )
        d = r.to_dict()
        assert d["score"] == 8.5
        assert "Dubbed" in d["dub_status"]


# ── content.py tests ──────────────────────────────────────────────


class TestContent:
    def test_episode_from_dict(self):
        from cli_anything.cloudstream.core.content import Episode

        data = {
            "data": "https://example.com/ep1",
            "name": "Pilot",
            "season": 1,
            "episode": 1,
            "runTime": 45,
        }
        ep = Episode.from_dict(data)
        assert ep.data == "https://example.com/ep1"
        assert ep.name == "Pilot"
        assert ep.season == 1
        assert ep.run_time == 45

    def test_episode_display_name(self):
        from cli_anything.cloudstream.core.content import Episode

        # Season + Episode + Name
        ep1 = Episode(data="", name="Pilot", season=1, episode=1)
        assert ep1.display_name == "S01E01 - Pilot"

        # Episode only
        ep2 = Episode(data="", episode=5)
        assert ep2.display_name == "E05"

        # Name only
        ep3 = Episode(data="", name="Movie")
        assert ep3.display_name == "Movie"

        # Nothing
        ep4 = Episode(data="")
        assert ep4.display_name == "Episode"

    def test_content_details_movie(self):
        from cli_anything.cloudstream.core.content import ContentDetails

        data = {
            "name": "Inception",
            "url": "http://x.com/inception",
            "apiName": "TMDB",
            "type": "Movie",
            "year": 2010,
            "plot": "A thief who steals corporate secrets...",
            "score": 8.8,
            "tags": ["Sci-Fi", "Action", "Thriller"],
            "duration": 148,
            "dataUrl": "http://x.com/inception/data",
        }
        content = ContentDetails.from_dict(data)
        assert content.name == "Inception"
        assert content.is_movie is True
        assert content.is_series is False
        assert content.year == 2010
        assert content.data_url is not None

    def test_content_details_series(self):
        from cli_anything.cloudstream.core.content import ContentDetails, Episode

        data = {
            "name": "Breaking Bad",
            "url": "http://x.com/bb",
            "apiName": "TMDB",
            "type": "TvSeries",
            "episodes": [
                {"data": "ep1", "name": "Pilot", "season": 1, "episode": 1},
                {"data": "ep2", "name": "Cat's in the Bag...", "season": 1, "episode": 2},
                {"data": "ep3", "name": "...And the Bag's in the River", "season": 1, "episode": 3},
            ],
            "showStatus": "Completed",
        }
        content = ContentDetails.from_dict(data)
        assert content.is_series is True
        assert content.total_episodes == 3
        assert content.show_status == "Completed"
        seasons = content.get_seasons()
        assert 1 in seasons

    def test_content_get_episode_by_index(self):
        from cli_anything.cloudstream.core.content import ContentDetails

        data = {
            "name": "Show",
            "url": "http://x.com",
            "apiName": "P",
            "type": "TvSeries",
            "episodes": [
                {"data": "ep0", "episode": 1},
                {"data": "ep1", "episode": 2},
                {"data": "ep2", "episode": 3},
            ],
        }
        content = ContentDetails.from_dict(data)
        ep = content.get_episode(index=1)
        assert ep is not None
        assert ep.data == "ep1"

    def test_content_get_episode_by_season(self):
        from cli_anything.cloudstream.core.content import ContentDetails

        data = {
            "name": "Show",
            "url": "http://x.com",
            "apiName": "P",
            "type": "TvSeries",
            "episodes": [
                {"data": "s1e1", "season": 1, "episode": 1},
                {"data": "s1e2", "season": 1, "episode": 2},
                {"data": "s2e1", "season": 2, "episode": 1},
            ],
        }
        content = ContentDetails.from_dict(data)
        ep = content.get_episode(season=2, episode=1)
        assert ep is not None
        assert ep.data == "s2e1"

    def test_format_content_info(self):
        from cli_anything.cloudstream.core.content import ContentDetails, format_content_info

        content = ContentDetails(
            name="Inception",
            url="http://x.com",
            api_name="TMDB",
            type="Movie",
            year=2010,
            score=8.8,
            tags=["Sci-Fi", "Action"],
            duration=148,
        )
        info = format_content_info(content)
        assert info["Title"] == "Inception"
        assert info["Year"] == "2010"
        assert "148 min" in info["Duration"]


# ── stream.py tests ───────────────────────────────────────────────


class TestStream:
    def test_stream_link_from_dict(self):
        from cli_anything.cloudstream.core.stream import StreamLink

        data = {
            "source": "FileMoon",
            "name": "FileMoon - 1080p",
            "url": "https://filemoon.sx/stream/abc.m3u8",
            "referer": "https://filemoon.sx",
            "quality": 1080,
            "type": "M3U8",
        }
        link = StreamLink.from_dict(data)
        assert link.source == "FileMoon"
        assert link.quality == 1080
        assert link.is_m3u8 is True

    def test_stream_link_quality_label(self):
        from cli_anything.cloudstream.core.stream import StreamLink

        assert StreamLink("", "", "", quality=2160).quality_label == "4K"
        assert StreamLink("", "", "", quality=1080).quality_label == "1080p"
        assert StreamLink("", "", "", quality=720).quality_label == "720p"
        assert StreamLink("", "", "", quality=480).quality_label == "480p"
        assert StreamLink("", "", "", quality=0).quality_label == "Unknown"

    def test_stream_result_best_quality(self):
        from cli_anything.cloudstream.core.stream import StreamLink, StreamResult

        result = StreamResult(links=[
            StreamLink("A", "A", "http://a", quality=720),
            StreamLink("B", "B", "http://b", quality=1080),
            StreamLink("C", "C", "http://c", quality=480),
        ])
        best = result.best_quality
        assert best is not None
        assert best.quality == 1080

    def test_stream_result_filter_by_type(self):
        from cli_anything.cloudstream.core.stream import StreamLink, StreamResult

        result = StreamResult(links=[
            StreamLink("A", "A", "http://a", quality=720, type="VIDEO"),
            StreamLink("B", "B", "http://b", quality=1080, type="M3U8"),
            StreamLink("C", "C", "http://c", quality=480, type="VIDEO"),
        ])
        m3u8_links = result.filter_by_type("M3U8")
        assert len(m3u8_links) == 1
        assert m3u8_links[0].type == "M3U8"

    def test_subtitle_track_from_dict(self):
        from cli_anything.cloudstream.core.stream import SubtitleTrack

        data = {"lang": "English", "url": "https://example.com/sub.srt"}
        sub = SubtitleTrack.from_dict(data)
        assert sub.lang == "English"
        assert sub.url.endswith(".srt")


# ── download.py tests ─────────────────────────────────────────────


class TestDownload:
    def test_sanitize_filename(self):
        from cli_anything.cloudstream.core.download import sanitize_filename

        assert sanitize_filename('Movie: The "Sequel"') == "Movie_ The _Sequel_"
        assert sanitize_filename("normal_name") == "normal_name"
        assert sanitize_filename("a/b\\c|d?e") == "a_b_c_d_e"

    def test_build_output_path(self):
        from cli_anything.cloudstream.core.download import build_output_path

        path = build_output_path("Inception", quality="1080p", output_dir="/tmp")
        assert path.endswith(".mp4")
        assert "Inception" in path
        assert "1080p" in path

    def test_build_output_path_with_episode(self):
        from cli_anything.cloudstream.core.download import build_output_path

        path = build_output_path("Breaking Bad", episode_label="S01E01",
                                 quality="720p", output_dir="/tmp")
        assert "S01E01" in path
        assert "Breaking Bad" in path

    def test_download_result_dataclass(self):
        from cli_anything.cloudstream.core.download import DownloadResult

        result = DownloadResult(status="ok", output="/tmp/movie.mp4",
                                file_size=1024000, method="yt-dlp")
        d = result.to_dict()
        assert d["status"] == "ok"
        assert d["file_size"] == 1024000


# ── session.py tests ──────────────────────────────────────────────


class TestSession:
    def test_session_create_and_save(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        sess_path = str(tmp_path / "session.json")
        sess = Session(session_path=sess_path)
        sess.save()
        assert os.path.exists(sess_path)

        with open(sess_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["version"] == "1.0.0"

    def test_session_search_results(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        sess = Session(session_path=str(tmp_path / "session.json"))
        sess.set_search_results("inception", [
            {"name": "Inception", "url": "http://x.com", "type": "Movie"},
        ])

        result = sess.get_search_result(1)
        assert result is not None
        assert result["name"] == "Inception"

        result_none = sess.get_search_result(99)
        assert result_none is None

    def test_session_history(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        sess = Session(session_path=str(tmp_path / "session.json"))
        sess.add_history("search", query="test1")
        sess.add_history("search", query="test2")
        sess.add_history("load", url="http://x.com")

        history = sess.get_history()
        assert len(history) == 3

        search_history = sess.get_history(action="search")
        assert len(search_history) == 2

    def test_session_favorites(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        sess = Session(session_path=str(tmp_path / "session.json"))
        sess.add_favorite("Inception", "http://inception.com", "Movie")
        sess.add_favorite("Naruto", "http://naruto.com", "Anime")

        favs = sess.list_favorites()
        assert len(favs) == 2

        # No duplicates
        sess.add_favorite("Inception", "http://inception.com", "Movie")
        assert len(sess.list_favorites()) == 2

        # Remove
        removed = sess.remove_favorite("http://inception.com")
        assert removed is True
        assert len(sess.list_favorites()) == 1

    def test_session_settings(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        sess = Session(session_path=str(tmp_path / "session.json"))
        sess.set_setting("preferred_quality", 1080)
        assert sess.get_setting("preferred_quality") == 1080
        assert sess.get_setting("nonexistent", "default") == "default"

    def test_session_reset(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        sess = Session(session_path=str(tmp_path / "session.json"))
        sess.add_favorite("Test", "http://test.com", "Movie")
        sess.add_history("search", query="test")
        assert len(sess.list_favorites()) == 1

        sess.reset()
        assert len(sess.list_favorites()) == 0
        assert len(sess.get_history()) == 0


# ── export.py tests ───────────────────────────────────────────────


class TestExport:
    def test_select_best_link(self):
        from cli_anything.cloudstream.core.stream import StreamLink, StreamResult
        from cli_anything.cloudstream.core.export import select_best_link

        result = StreamResult(links=[
            StreamLink("A", "A", "http://a", quality=720, type="VIDEO"),
            StreamLink("B", "B", "http://b", quality=1080, type="M3U8"),
            StreamLink("C", "C", "http://c", quality=480, type="VIDEO"),
        ])

        best = select_best_link(result, preferred_quality=1080)
        assert best is not None
        assert best.quality == 1080

    def test_select_best_link_type_preference(self):
        from cli_anything.cloudstream.core.stream import StreamLink, StreamResult
        from cli_anything.cloudstream.core.export import select_best_link

        result = StreamResult(links=[
            StreamLink("A", "A", "http://a", quality=1080, type="VIDEO"),
            StreamLink("B", "B", "http://b", quality=1080, type="M3U8"),
        ])

        # VIDEO preferred over M3U8 at same quality
        best = select_best_link(result, preferred_quality=1080)
        assert best is not None
        assert best.type == "VIDEO"

    def test_build_batch_download_plan(self):
        from cli_anything.cloudstream.core.content import ContentDetails, Episode
        from cli_anything.cloudstream.core.export import build_batch_download_plan, ExportConfig

        content = ContentDetails(
            name="Show",
            url="http://x.com",
            api_name="P",
            type="TvSeries",
            episodes=[
                Episode(data="ep1", name="Pilot", season=1, episode=1),
                Episode(data="ep2", name="Second", season=1, episode=2),
            ],
        )

        plan = build_batch_download_plan(content, config=ExportConfig(output_dir="/tmp"))
        assert len(plan) == 2
        assert plan[0]["episode"] == "S01E01 - Pilot"
        assert plan[1]["episode"] == "S01E02 - Second"
