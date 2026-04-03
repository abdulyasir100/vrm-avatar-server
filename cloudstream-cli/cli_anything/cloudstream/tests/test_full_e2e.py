"""E2E and subprocess tests for CloudStream CLI harness.

Tests backend availability, session persistence, and CLI subprocess invocation.
Backend tests require Java, yt-dlp, and/or the CloudStream JVM JAR.
Subprocess tests use _resolve_cli() per HARNESS.md specification.
"""

import json
import os
import subprocess
import sys
import tempfile

import pytest


# ── CLI resolution helper ─────────────────────────────────────────


def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    import shutil

    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = name.replace("cli-anything-", "cli_anything.") + "." + name.split("-")[-1] + "_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


# ── Backend availability tests ────────────────────────────────────


class TestBackendAvailability:
    def test_check_backend_status(self):
        from cli_anything.cloudstream.utils.cloudstream_backend import check_backend_status

        status = check_backend_status()
        assert isinstance(status, dict)
        assert "scrapers" in status
        assert "extractors" in status
        assert status["scrapers"]["available"] is True
        assert status["extractors"]["available"] is True

    def test_scrapers_registered(self):
        """Test scrapers are properly registered."""
        from cli_anything.cloudstream.core.scrapers.base import ScraperRegistry
        from cli_anything.cloudstream.utils.cloudstream_backend import _init
        _init()

        scrapers = ScraperRegistry.all()
        assert len(scrapers) >= 2
        names = [s.name for s in scrapers]
        assert "GogoAnime" in names
        assert "HiAnime" in names

    def test_extractors_registered(self):
        """Test extractors are properly registered."""
        from cli_anything.cloudstream.core.extractors.base import ExtractorRegistry
        from cli_anything.cloudstream.utils.cloudstream_backend import _init
        _init()

        extractors = ExtractorRegistry.all()
        assert len(extractors) >= 3
        names = [e.name for e in extractors]
        assert "FileMoon" in names
        assert "StreamWish" in names
        assert "Mp4Upload" in names


# ── Provider operation tests ──────────────────────────────────────


class TestProviderOperations:
    def test_list_providers_returns_data(self):
        from cli_anything.cloudstream.core.provider import list_providers

        providers = list_providers()
        assert len(providers) >= 2
        assert all(p.name for p in providers)
        assert all(p.main_url for p in providers)

    def test_list_extractors_returns_data(self):
        from cli_anything.cloudstream.core.provider import list_extractors

        extractors = list_extractors()
        assert len(extractors) >= 3
        assert all(e.name for e in extractors)

    def test_provider_info_structure(self):
        from cli_anything.cloudstream.core.provider import list_providers

        providers = list_providers()
        for p in providers:
            d = p.to_dict()
            assert "name" in d
            assert "main_url" in d
            assert "lang" in d
            assert "supported_types" in d
            assert isinstance(d["supported_types"], list)


# ── Session persistence tests ─────────────────────────────────────


class TestSessionPersistence:
    def test_session_roundtrip(self, tmp_path):
        """Session state survives save and reload."""
        from cli_anything.cloudstream.core.session import Session

        path = str(tmp_path / "session.json")

        # Create and populate
        sess1 = Session(session_path=path)
        sess1.set_search_results("test query", [{"name": "Result1", "url": "http://x.com"}])
        sess1.set_setting("quality", 720)
        sess1.save()

        # Reload
        sess2 = Session(session_path=path)
        assert sess2.state.last_search_query == "test query"
        assert len(sess2.state.last_search_results) == 1
        assert sess2.get_setting("quality") == 720

    def test_session_favorites_persist(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        path = str(tmp_path / "session.json")

        sess1 = Session(session_path=path)
        sess1.add_favorite("Movie1", "http://movie1.com", "Movie")
        sess1.add_favorite("Anime1", "http://anime1.com", "Anime")

        sess2 = Session(session_path=path)
        favs = sess2.list_favorites()
        assert len(favs) == 2
        assert favs[0].name == "Movie1"
        assert favs[1].type == "Anime"

    def test_session_history_persist(self, tmp_path):
        from cli_anything.cloudstream.core.session import Session

        path = str(tmp_path / "session.json")

        sess1 = Session(session_path=path)
        sess1.add_history("search", query="inception")
        sess1.add_history("load", url="http://x.com")

        sess2 = Session(session_path=path)
        history = sess2.get_history()
        assert len(history) == 2
        assert history[0].action == "search"
        assert history[1].action == "load"


# ── CLI subprocess tests ──────────────────────────────────────────


class TestCLISubprocess:
    CLI_BASE = _resolve_cli("cli-anything-cloudstream")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_cli_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "cloudstream" in result.stdout.lower() or "CLI" in result.stdout

    def test_cli_version(self):
        result = self._run(["--version"])
        assert result.returncode == 0
        assert "1.0.0" in result.stdout

    def test_cli_json_provider_list(self):
        result = self._run(["--json", "provider", "list"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "providers" in data
        assert isinstance(data["providers"], list)
        assert data["total"] >= 1

    def test_cli_json_extractor_list(self):
        result = self._run(["--json", "extractor", "list"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "extractors" in data
        assert data["total"] >= 3

    def test_cli_json_status(self):
        result = self._run(["--json", "status"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "scrapers" in data
        assert "extractors" in data

    def test_cli_json_session_info(self):
        result = self._run(["--json", "session", "info"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "version" in data
        assert "history_entries" in data
