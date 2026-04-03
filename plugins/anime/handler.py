"""Anime plugin — find anime via cloudstream CLI."""

import json
import logging
import asyncio
from typing import Any

logger = logging.getLogger(__name__)


async def _run_cli(*args: str) -> dict | None:
    cmd = ["cli-anything-cloudstream", "--json", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.warning(f"[anime] CLI error: {stderr.decode().strip()}")
            return None
        return json.loads(stdout.decode())
    except asyncio.TimeoutError:
        proc.kill()
        return None
    except Exception as e:
        logger.warning(f"[anime] CLI failed: {e}")
        return None


async def handle_find_anime(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    parts = [p.strip() for p in arg.split("|")]
    query = parts[0]
    episode_str = parts[1] if len(parts) > 1 else None
    episode = int(episode_str) if episode_str and episode_str.isdigit() else None
    latest = bool(episode_str and episode_str.lower() == "latest")

    search = await _run_cli("search", query)
    if not search or not search.get("results"):
        return {"ok": False, "result": f"No anime found for '{query}'", "side_effects": []}

    results = search["results"]

    if episode is None and not latest:
        lines = []
        for i, r in enumerate(results[:5], 1):
            year = f" ({r['year']})" if r.get("year") else ""
            lines.append(f"{i}. {r['name']}{year} - {r['url']}")
        return {"ok": True, "result": "Search results:\n" + "\n".join(lines), "side_effects": []}

    url = results[0]["url"]
    content = await _run_cli("load", url)
    if not content or not content.get("episodes"):
        return {"ok": False, "result": f"Could not load episodes for '{results[0]['name']}'", "side_effects": []}

    title = content.get("name", results[0]["name"])

    if latest:
        ep = content["episodes"][-1]
        episode = ep.get("episode", len(content["episodes"]))
    else:
        ep = next((e for e in content["episodes"] if e.get("episode") == episode), None)

    if not ep:
        total = content.get("total_episodes", len(content["episodes"]))
        return {"ok": False, "result": f"{title} has {total} episodes, episode {episode} not found", "side_effects": []}

    links = await _run_cli("links", ep["data"])
    if not links or not links.get("links"):
        return {"ok": False, "result": f"No streaming links found for {title} EP{episode}", "side_effects": []}

    seen = set()
    picked = []
    for link in links["links"]:
        source = link.get("source", "Unknown").replace("Choose this server", "").strip()
        if source not in seen:
            seen.add(source)
            picked.append(f"- {source}: {link['url']}")
        if len(picked) >= 3:
            break
    return {"ok": True, "result": f"{title} EP{episode}:\n" + "\n".join(picked), "side_effects": []}


async def cmd_search(args: str = "") -> dict:
    if not args:
        return {"text": "Usage: /p.anime <title>\nExample: /p.anime frieren", "inline_keyboard": None}
    result = await handle_find_anime(args, {})
    return {"text": result["result"], "inline_keyboard": None}
