"""Google OAuth for a bot whose only screen is a chat window.

No listener, no device flow: the consent URL redirects to a dead loopback address, the browser
fails to load it, and the user pastes that address-bar URL back. State + PKCE verifier are saved
between the two steps, so they can be two separate chat messages (or two process lifetimes).

Flow ported from Hermes Agent's skills/productivity/google-workspace/scripts/setup.py
(https://github.com/NousResearch/hermes-agent, MIT License, Copyright (c) 2025 Nous Research).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config

REDIRECT_URI = "http://localhost:1"  # Google retired the OOB flow; a Desktop client accepts any localhost port
_SCOPE_BASE = "https://www.googleapis.com/auth/"
SCOPES = [_SCOPE_BASE + s for s in ("gmail.readonly", "gmail.send", "gmail.modify", "calendar")]


class AuthError(Exception):
    """Something the user can act on; the message is shown to them as-is."""


def _dir() -> Path:
    path = Path(config.PLUGIN_DATA_DIR) / "google"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _client_secret_path() -> Path:
    return _dir() / "client_secret.json"


def _token_path() -> Path:
    return _dir() / "token.json"


def _pending_path() -> Path:
    return _dir() / "oauth_pending.json"


def _client_config() -> dict:
    """The OAuth client: a downloaded client_secret.json, or GOOGLE_CLIENT_ID/SECRET from the env."""
    if _client_secret_path().is_file():
        data = json.loads(_client_secret_path().read_text(encoding="utf-8"))
        if "installed" in data or "web" in data:
            return data
        raise AuthError("client_secret.json is not a Google OAuth client file (no 'installed' or 'web' key).")
    client_id, secret = os.environ.get("GOOGLE_CLIENT_ID", ""), os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if client_id and secret:
        return {"installed": {"client_id": client_id, "client_secret": secret,
                              "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                              "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": [REDIRECT_URI]}}
    raise AuthError(f"No Google OAuth client yet. Create a 'Desktop app' OAuth client in Google Cloud Console and "
                    f"either save its JSON as {_client_secret_path()} or set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")


def _flow(scopes: list[str], **kwargs):
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(_client_config(), scopes=scopes, redirect_uri=REDIRECT_URI, **kwargs)


def auth_url() -> str:
    """Step 1: the consent URL to send to the user. Saves what step 2 needs."""
    flow = _flow(SCOPES, autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    _pending_path().write_text(json.dumps({"state": state, "code_verifier": flow.code_verifier}), encoding="utf-8")
    return url


def parse_callback(code_or_url: str) -> tuple[str, str | None, list[str]]:
    """(code, state, granted scopes) from the pasted redirect URL — or from a bare code."""
    text = code_or_url.strip()
    if not text.startswith("http"):
        return text, None, []
    params = parse_qs(urlparse(text).query)
    if "error" in params:
        raise AuthError(f"Google refused: {params['error'][0]}.")
    if "code" not in params:
        raise AuthError("That URL has no 'code' in it. Paste the whole address from the browser after approving.")
    return params["code"][0], (params.get("state") or [None])[0], (params.get("scope") or [""])[0].split()


def exchange(code_or_url: str) -> list[str]:
    """Step 2: trade the pasted code for a token. Returns the scopes that are missing, if any."""
    if not _pending_path().is_file():
        raise AuthError("No sign-in in progress. Start one with /p.google.connect.")
    pending = json.loads(_pending_path().read_text(encoding="utf-8"))
    code, state, granted = parse_callback(code_or_url)
    if state and state != pending["state"]:
        raise AuthError("That link belongs to an older sign-in. Run /p.google.connect again for a fresh one.")

    flow = _flow(granted or SCOPES, state=pending["state"], code_verifier=pending["code_verifier"])
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # he may untick a permission on the consent screen
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        raise AuthError(f"Google rejected the code ({e}). Codes are single-use and short-lived — "
                        f"run /p.google.connect again.") from e

    payload = json.loads(flow.credentials.to_json())
    payload["type"] = "authorized_user"
    # Store what was actually granted: loading with scopes he declined makes every refresh fail with invalid_scope.
    payload["scopes"] = list(getattr(flow.credentials, "granted_scopes", None) or granted or SCOPES)
    _token_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _pending_path().unlink(missing_ok=True)
    return [s for s in SCOPES if s not in payload["scopes"]]


def credentials():
    """Valid credentials, refreshed when due. Raises AuthError when he has to sign in (again)."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not _token_path().is_file():
        raise AuthError("Google is not connected. Use /p.google.connect.")
    try:
        stored = json.loads(_token_path().read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(stored, stored.get("scopes"))
    except (ValueError, KeyError) as e:
        raise AuthError(f"The saved Google token is unreadable ({e}). Use /p.google.connect.") from e
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AuthError(f"Google no longer accepts the saved sign-in ({e}). Use /p.google.connect. "
                            f"(An OAuth app left in 'Testing' expires its tokens after 7 days — publish it.)") from e
        payload = json.loads(creds.to_json())
        payload.update(type="authorized_user", scopes=stored.get("scopes"))
        _token_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if not creds.valid:
        raise AuthError("The saved Google sign-in is not valid. Use /p.google.connect.")
    return creds


def status() -> str:
    try:
        creds = credentials()
    except AuthError as e:
        return f"Not connected — {e}"
    missing = [s.removeprefix(_SCOPE_BASE) for s in SCOPES if s not in (creds.scopes or [])]
    return "Connected." + (f" Missing permissions: {', '.join(missing)}." if missing else "")


def revoke() -> None:
    import httpx
    if _token_path().is_file():
        try:
            token = json.loads(_token_path().read_text(encoding="utf-8")).get("refresh_token")
            if token:
                httpx.post("https://oauth2.googleapis.com/revoke", params={"token": token}, timeout=10)
        except Exception:
            pass  # the local copy goes either way
    _token_path().unlink(missing_ok=True)
    _pending_path().unlink(missing_ok=True)
