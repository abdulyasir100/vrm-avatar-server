"""Notion plugin — search, read and write pages with an internal integration token.

No OAuth: create an integration at notion.so/my-integrations, put its token in NOTION_API_KEY, and
share each page or database with it (page menu -> Connect to). An unshared page answers 404.
Endpoint set after Hermes Agent's notion skill (MIT, Copyright (c) 2025 Nous Research).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from services import turn_context

logger = logging.getLogger(__name__)

_BASE = "https://api.notion.com/v1"
_VERSION = "2025-09-03"  # databases are "data sources" from this version on
_PAGE_CHARS = 3000
_ID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.IGNORECASE)


def _result(ok: bool, text: str) -> dict[str, Any]:
    return {"ok": ok, "result": text, "side_effects": []}


def page_id(text: str) -> str:
    """The page id inside a Notion URL or a bare id. '' when there is none."""
    match = _ID.search(text or "")
    return match.group(0).replace("-", "") if match else ""


async def _call(method: str, path: str, body: dict | None = None) -> dict:
    token = os.environ.get("NOTION_API_KEY", "")
    if not token:
        raise RuntimeError("Notion is not set up: NOTION_API_KEY is missing.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(method, _BASE + path, json=body, headers={
            "Authorization": f"Bearer {token}", "Notion-Version": _VERSION, "Content-Type": "application/json"})
    if response.status_code == 404:
        raise RuntimeError("Notion says not found — share that page with the integration (page menu -> Connect to).")
    if response.status_code >= 400:
        raise RuntimeError(f"Notion error {response.status_code}: {response.text[:200]}")
    return response.json()


async def _guarded(context: dict, coroutine_fn, *args) -> dict[str, Any]:
    if turn_context.is_autonomous(context or {}):
        return _result(False, "Notion tools only run when the user asks in chat.")
    try:
        return _result(True, await coroutine_fn(*args))
    except Exception as e:
        logger.warning(f"[notion] {coroutine_fn.__name__} failed: {e!r}")
        return _result(False, str(e))


def _title(item: dict) -> str:
    for prop in (item.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", [])) or "(untitled)"
    return "".join(t.get("plain_text", "") for t in item.get("title", [])) or "(untitled)"


async def _search(query: str) -> str:
    found = (await _call("POST", "/search", {"query": query, "page_size": 8})).get("results", [])
    if not found:
        return "Nothing in Notion matches that (only pages shared with the integration are visible)."
    return "\n".join(f"- {item['object']} {item['id']} | {_title(item)}" for item in found)


async def _read(ref: str) -> str:
    pid = page_id(ref)
    if not pid:
        raise RuntimeError("Give me a Notion page id or URL.")
    page = await _call("GET", f"/pages/{pid}/markdown")
    text = page.get("markdown") or ""
    return text[:_PAGE_CHARS] + ("\n...(truncated)" if len(text) > _PAGE_CHARS else "") or "(empty page)"


async def _append(ref: str, text: str) -> str:
    pid = page_id(ref)
    if not pid or not text.strip():
        raise RuntimeError("Format: <page id or URL>|text to append")
    blocks = [{"object": "block", "type": "paragraph",
               "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk[:1900]}}]}}
              for chunk in text.strip().split("\n\n")]
    await _call("PATCH", f"/blocks/{pid}/children", {"children": blocks})
    return "Appended."


async def _create(parent_ref: str, title: str, markdown: str) -> str:
    pid = page_id(parent_ref)
    if not pid or not title.strip():
        raise RuntimeError("Format: <parent page id or URL>|title|markdown body")
    page = await _call("POST", "/pages", {"parent": {"page_id": pid}, "markdown": markdown,
                                          "properties": {"title": [{"text": {"content": title.strip()}}]}})
    return f"Created: {page.get('url') or page.get('id')}"


async def handle_notion_search(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    return await _guarded(context, _search, arg.strip())


async def handle_notion_read(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    return await _guarded(context, _read, arg)


async def handle_notion_append(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    ref, _, text = arg.partition("|")
    return await _guarded(context, _append, ref, text)


async def handle_notion_create(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    parts = arg.split("|", 2) + ["", ""]
    return await _guarded(context, _create, parts[0], parts[1], parts[2])


async def cmd_status(args: str = "") -> dict:
    try:
        me = await _call("GET", "/users/me")
        text = f"Notion: connected as integration '{me.get('name') or me.get('id')}'."
    except Exception as e:
        text = f"Notion: {e}"
    return {"text": text, "inline_keyboard": None}
