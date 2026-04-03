"""Spotify plugin — play character songs, control playback."""

import logging
from typing import Any

import config
from plugins.spotify import spotify_client, catalog

logger = logging.getLogger(__name__)

_storage = None


def set_storage(storage_module):
    global _storage
    _storage = storage_module
    spotify_client.set_storage(storage_module)


def _get_storage():
    if _storage is None:
        raise RuntimeError("Spotify storage not initialized")
    return _storage


async def handle_play_song(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Play a song from the character's catalogue."""
    if not spotify_client.is_authenticated():
        return {"ok": False, "result": "Spotify not connected. Use /p.spotify.auth to set up.", "side_effects": []}

    query = arg.strip()
    if not query:
        return {"ok": False, "result": "What song should I play?", "side_effects": []}

    # Random from catalogue
    if query.lower() in ("random", "shuffle", "anything", "surprise me"):
        song_title = catalog.get_random()
    else:
        # Try matching from catalogue first
        song_title = catalog.find(query)

    if not song_title:
        # Not in catalogue — reject in character
        char_name = config.CHARACTER_NAME
        return {
            "ok": False,
            "result": f"That's not one of my songs. I only play {char_name}'s catalogue.",
            "side_effects": [],
        }

    # Search on Spotify
    char_name = config.CHARACTER_NAME
    results = await spotify_client.search_track(f"{char_name} {song_title}")
    if not results:
        return {"ok": False, "result": f"Couldn't find '{song_title}' on Spotify.", "side_effects": []}

    # Play the first result
    track = results[0]
    success = await spotify_client.play_uri(track["uri"])
    if not success:
        return {"ok": False, "result": "Failed to start playback. Is Spotify open on a device?", "side_effects": []}

    return {
        "ok": True,
        "result": f"Now playing: {track['name']} — {track['artist']}",
        "side_effects": [],
    }


async def handle_check_spotify(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Show what's currently playing."""
    if not spotify_client.is_authenticated():
        return {"ok": False, "result": "Spotify not connected. Use /p.spotify.auth to set up.", "side_effects": []}

    playing = await spotify_client.get_now_playing()
    if not playing:
        return {"ok": True, "result": "Nothing playing right now.", "side_effects": []}

    status = "Playing" if playing["is_playing"] else "Paused"
    return {
        "ok": True,
        "result": f"{status}: {playing['name']} — {playing['artist']}\nAlbum: {playing['album']}",
        "side_effects": [],
    }


# --- Telegram commands ---

async def cmd_now(args: str = "") -> dict:
    """Show now playing with controls."""
    if not spotify_client.is_authenticated():
        return {"text": "Spotify not connected. Use /p.spotify.auth", "inline_keyboard": None}

    playing = await spotify_client.get_now_playing()
    if not playing:
        return {"text": "Nothing playing.", "inline_keyboard": None}

    status = "Playing" if playing["is_playing"] else "Paused"
    text = f"{status}: {playing['name']} — {playing['artist']}\nAlbum: {playing['album']}"

    keyboard = []
    if playing["is_playing"]:
        keyboard.append([
            {"text": "Pause", "callback_data": "plugin:spotify:pause:0"},
            {"text": "Skip", "callback_data": "plugin:spotify:skip:0"},
        ])
    else:
        keyboard.append([
            {"text": "Resume", "callback_data": "plugin:spotify:resume:0"},
            {"text": "Skip", "callback_data": "plugin:spotify:skip:0"},
        ])

    return {"text": text, "inline_keyboard": keyboard if keyboard else None}


async def cmd_play(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.spotify.play <song name>\nOr: /p.spotify.play random", "inline_keyboard": None}
    result = await handle_play_song(args, {})
    return {"text": result["result"], "inline_keyboard": None}


async def cmd_pause(args: str = "") -> dict:
    if not spotify_client.is_authenticated():
        return {"text": "Spotify not connected.", "inline_keyboard": None}

    playing = await spotify_client.get_now_playing()
    if playing and playing["is_playing"]:
        await spotify_client.pause()
        return {"text": f"Paused: {playing['name']}", "inline_keyboard": None}
    else:
        await spotify_client.resume()
        return {"text": "Resumed playback.", "inline_keyboard": None}


async def cmd_skip(args: str = "") -> dict:
    if not spotify_client.is_authenticated():
        return {"text": "Spotify not connected.", "inline_keyboard": None}
    await spotify_client.skip_next()
    return {"text": "Skipped to next track.", "inline_keyboard": None}


async def cmd_catalog(args: str = "") -> dict:
    songs = catalog.get_songs()
    lines = [f"{i}. {s}" for i, s in enumerate(songs, 1)]
    char_name = config.CHARACTER_NAME
    return {"text": f"{char_name}'s Songs ({len(songs)}):\n\n" + "\n".join(lines), "inline_keyboard": None}


async def cmd_auth(args: str = "") -> dict:
    """Show auth URL or exchange code."""
    if args.strip():
        # User is passing the auth code
        code = args.strip()
        success = await spotify_client.exchange_code(code)
        if success:
            return {"text": "Spotify connected successfully!", "inline_keyboard": None}
        return {"text": "Auth failed. Check the code and try again.", "inline_keyboard": None}

    if spotify_client.is_authenticated():
        return {"text": "Spotify is already connected.", "inline_keyboard": None}

    url = spotify_client.get_auth_url()
    return {
        "text": f"1. Open this URL:\n{url}\n\n2. Authorize and copy the code from the redirect URL\n\n3. Run: /p.spotify.auth <code>",
        "inline_keyboard": None,
    }


async def handle_callback(action: str, item_id: str) -> dict | None:
    if action == "pause":
        await spotify_client.pause()
        return {"message": "Paused.", "refresh": True}
    if action == "resume":
        await spotify_client.resume()
        return {"message": "Resumed.", "refresh": True}
    if action == "skip":
        await spotify_client.skip_next()
        return {"message": "Skipped.", "refresh": True}
    return None
