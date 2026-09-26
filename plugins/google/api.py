"""Gmail + Google Calendar calls. Blocking (google-api-python-client) — callers use asyncio.to_thread.

Command surface after Hermes Agent's google_api.py (MIT, Copyright (c) 2025 Nous Research).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from plugins.google import oauth

_BODY_CHARS = 2500


def _service(name: str, version: str):
    from googleapiclient.discovery import build
    return build(name, version, credentials=oauth.credentials(), cache_discovery=False)


def _headers(message: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _body(payload: dict) -> str:
    """text/plain if any part has it, else text/html — walking nested multiparts too."""
    found: dict[str, str] = {}

    def walk(part: dict) -> None:
        data = part.get("body", {}).get("data")
        if data and part.get("mimeType") in ("text/plain", "text/html"):
            found.setdefault(part["mimeType"], _decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return found.get("text/plain") or found.get("text/html") or ""


# --- Gmail ---

def gmail_search(query: str, limit: int = 8) -> list[dict]:
    gmail = _service("gmail", "v1")
    hits = gmail.users().messages().list(userId="me", q=query, maxResults=limit).execute().get("messages", [])
    results = []
    for hit in hits:
        message = gmail.users().messages().get(userId="me", id=hit["id"], format="metadata",
                                               metadataHeaders=["From", "Subject", "Date"]).execute()
        headers = _headers(message)
        results.append({"id": message["id"], "from": headers.get("from", ""), "subject": headers.get("subject", ""),
                        "date": headers.get("date", ""), "snippet": message.get("snippet", ""),
                        "unread": "UNREAD" in message.get("labelIds", [])})
    return results


def gmail_read(message_id: str) -> dict:
    message = _service("gmail", "v1").users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = _headers(message)
    body = " ".join(_body(message.get("payload", {})).split())
    return {"id": message["id"], "thread_id": message.get("threadId", ""), "from": headers.get("from", ""),
            "to": headers.get("to", ""), "subject": headers.get("subject", ""), "date": headers.get("date", ""),
            "message_id_header": headers.get("message-id", ""),
            "body": body[:_BODY_CHARS] + ("..." if len(body) > _BODY_CHARS else "")}


def gmail_send(to: str, subject: str, body: str, reply_to_id: str | None = None) -> str:
    """Send a mail (a threaded reply when reply_to_id is given). Returns the new message id."""
    gmail = _service("gmail", "v1")
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"], mime["Subject"] = to, subject
    request: dict = {}
    if reply_to_id:
        original = gmail_read(reply_to_id)
        if original["message_id_header"]:
            mime["In-Reply-To"] = mime["References"] = original["message_id_header"]
        request["threadId"] = original["thread_id"]
    request["raw"] = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    return gmail.users().messages().send(userId="me", body=request).execute()["id"]


# --- Calendar ---

def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def calendar_list(days: int = 7, limit: int = 15) -> list[dict]:
    now = datetime.now(timezone.utc)
    events = _service("calendar", "v3").events().list(
        calendarId="primary", timeMin=_iso(now), timeMax=_iso(now + timedelta(days=days)), maxResults=limit,
        singleEvents=True, orderBy="startTime").execute().get("items", [])
    return [{"id": e["id"], "summary": e.get("summary", "(no title)"), "location": e.get("location", ""),
             "start": e["start"].get("dateTime") or e["start"].get("date"),
             "end": e["end"].get("dateTime") or e["end"].get("date")} for e in events]


def calendar_create(summary: str, start: str, end: str, location: str = "", description: str = "") -> str:
    """start/end are ISO 8601 with a UTC offset. Returns the event link."""
    event = {"summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}}
    if location:
        event["location"] = location
    if description:
        event["description"] = description
    return _service("calendar", "v3").events().insert(calendarId="primary", body=event).execute().get("htmlLink", "")
