"""Spotify Web API client — auth, playback, search."""

import os
import logging
import base64
import httpx
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Spotify API endpoints
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

# Scopes needed for playback control
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"

# Config from env
CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")

_storage = None
_access_token = None
_token_expiry = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module


def get_auth_url() -> str:
    """Generate Spotify OAuth URL for user to visit."""
    return (
        f"{AUTH_URL}?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&scope={SCOPES.replace(' ', '%20')}"
        f"&redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}"
    )


async def exchange_code(code: str) -> bool:
    """Exchange auth code for access + refresh tokens."""
    if not _storage:
        return False

    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Authorization": f"Basic {auth_header}"},
        )

    if resp.status_code != 200:
        logger.error(f"[spotify] Token exchange failed: {resp.text}")
        return False

    data = resp.json()
    _storage.set_token("access_token", data["access_token"])
    _storage.set_token("refresh_token", data["refresh_token"])
    _storage.set_token("token_expiry", str(int(datetime.now(timezone.utc).timestamp()) + data["expires_in"]))

    global _access_token, _token_expiry
    _access_token = data["access_token"]
    _token_expiry = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])

    logger.info("[spotify] Auth tokens saved")
    return True


async def _refresh_token() -> bool:
    """Refresh the access token using the refresh token."""
    global _access_token, _token_expiry

    if not _storage:
        return False

    refresh = _storage.get_token("refresh_token")
    if not refresh:
        return False

    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                },
                headers={"Authorization": f"Basic {auth_header}"},
            )

        if resp.status_code != 200:
            logger.error(f"[spotify] Token refresh failed: {resp.text}")
            return False

        data = resp.json()
        _access_token = data["access_token"]
        _token_expiry = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])

        _storage.set_token("access_token", _access_token)
        _storage.set_token("token_expiry", str(int(_token_expiry.timestamp())))

        if "refresh_token" in data:
            _storage.set_token("refresh_token", data["refresh_token"])

        return True
    except Exception as e:
        logger.error(f"[spotify] Token refresh error: {e}")
        return False


async def _get_token() -> str | None:
    """Get a valid access token, refreshing if needed."""
    global _access_token, _token_expiry

    if _access_token and _token_expiry and datetime.now(timezone.utc) < _token_expiry:
        return _access_token

    # Try loading from storage
    if _storage:
        stored = _storage.get_token("access_token")
        expiry = _storage.get_token("token_expiry")
        if stored and expiry:
            exp_time = datetime.fromtimestamp(int(expiry), tz=timezone.utc)
            if datetime.now(timezone.utc) < exp_time:
                _access_token = stored
                _token_expiry = exp_time
                return _access_token

    # Need to refresh
    if await _refresh_token():
        return _access_token

    return None


async def _api(method: str, path: str, json_data: dict = None) -> dict | None:
    """Make an authenticated Spotify API call."""
    token = await _get_token()
    if not token:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(
                method, f"{API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json_data,
            )

        if resp.status_code == 401:
            # Token expired mid-request, refresh and retry
            if await _refresh_token():
                token = _access_token
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.request(
                        method, f"{API_BASE}{path}",
                        headers={"Authorization": f"Bearer {token}"},
                        json=json_data,
                    )

        if resp.status_code in (200, 204):
            return resp.json() if resp.content else {"ok": True}
        logger.warning(f"[spotify] API {method} {path}: {resp.status_code} {resp.text[:100]}")
        return None
    except Exception as e:
        logger.error(f"[spotify] API error: {e}")
        return None


def is_authenticated() -> bool:
    """Check if we have stored tokens."""
    if not _storage:
        return False
    return _storage.get_token("refresh_token") is not None


# --- Playback ---

async def get_now_playing() -> dict | None:
    """Get currently playing track."""
    data = await _api("GET", "/me/player/currently-playing")
    if not data or not data.get("item"):
        return None
    item = data["item"]
    return {
        "name": item["name"],
        "artist": ", ".join(a["name"] for a in item["artists"]),
        "album": item["album"]["name"],
        "is_playing": data.get("is_playing", False),
        "uri": item["uri"],
        "url": item["external_urls"].get("spotify", ""),
    }


async def search_track(query: str, limit: int = 5) -> list[dict]:
    """Search for tracks."""
    data = await _api("GET", f"/search?q={query}&type=track&limit={limit}")
    if not data or "tracks" not in data:
        return []
    return [
        {
            "name": t["name"],
            "artist": ", ".join(a["name"] for a in t["artists"]),
            "uri": t["uri"],
            "url": t["external_urls"].get("spotify", ""),
        }
        for t in data["tracks"]["items"]
    ]


async def play_uri(uri: str) -> bool:
    """Play a specific track URI."""
    result = await _api("PUT", "/me/player/play", {"uris": [uri]})
    return result is not None


async def pause() -> bool:
    result = await _api("PUT", "/me/player/pause")
    return result is not None


async def resume() -> bool:
    result = await _api("PUT", "/me/player/play")
    return result is not None


async def skip_next() -> bool:
    result = await _api("POST", "/me/player/next")
    return result is not None
