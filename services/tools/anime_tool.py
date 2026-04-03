"""Anime tool — find anime episodes and streaming links via cloudstream CLI."""

import json
import logging
import asyncio
from typing import Any
from services import tool_registry

logger = logging.getLogger(__name__)


async def _run_cli(*args: str) -> dict | None:
    """Run cli-anything-cloudstream with --json flag and return parsed output."""
    cmd = ["cli-anything-cloudstream", "--json", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.warning(f"[anime] CLI error: {stderr.decode().strip()}")
            return None
        return json.loads(stdout.decode())
    except asyncio.TimeoutError:
        logger.warning("[anime] CLI timed out")
        proc.kill()
        return None
    except Exception as e:
        logger.warning(f"[anime] CLI failed: {e}")
        return None


def _find_episode(episodes: list[dict], target_ep: int) -> dict | None:
    """Find episode by number."""
    for ep in episodes:
        if ep.get("episode") == target_ep:
            return ep
    return None


def _format_search_results(results: list[dict]) -> str:
    """Format search results for LLM response."""
    lines = []
    for i, r in enumerate(results[:5], 1):
        year = f" ({r['year']})" if r.get("year") else ""
        lines.append(f"{i}. {r['name']}{year} — {r['url']}")
    return "Search results:\n" + "\n".join(lines)


def _format_links(links: list[dict], title: str, ep_label: str) -> str:
    """Format streaming links for LLM response."""
    if not links:
        return f"No streaming links found for {title} {ep_label}"
    seen = set()
    picked = []
    for link in links:
        source = link.get("source", "Unknown").replace("Choose this server", "").strip()
        if source not in seen:
            seen.add(source)
            picked.append(f"- {source}: {link['url']}")
        if len(picked) >= 3:
            break
    return f"{title} {ep_label}:\n" + "\n".join(picked)


async def handle_find_anime(arg: str, context: dict[str, Any]) -> dict[str, Any]:
    """Find anime episodes and get streaming URLs.

    Arg format: "title" for search, or "title|episode" for specific episode.
    """
    parts = [p.strip() for p in arg.split("|")]
    query = parts[0]
    episode = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

    # Step 1: Search
    search = await _run_cli("search", query)
    if not search or not search.get("results"):
        return {"ok": False, "result": f"No anime found for '{query}'", "side_effects": []}

    results = search["results"]

    # Search-only mode
    if episode is None:
        return {"ok": True, "result": _format_search_results(results), "side_effects": []}

    # Step 2: Load first result
    url = results[0]["url"]
    content = await _run_cli("load", url)
    if not content or not content.get("episodes"):
        return {"ok": False, "result": f"Could not load episodes for '{results[0]['name']}'", "side_effects": []}

    title = content.get("name", results[0]["name"])
    ep = _find_episode(content["episodes"], episode)
    if not ep:
        total = content.get("total_episodes", len(content["episodes"]))
        return {"ok": False, "result": f"{title} has {total} episodes, episode {episode} not found", "side_effects": []}

    # Step 3: Get streaming links
    links = await _run_cli("links", ep["data"])
    if not links or not links.get("links"):
        return {"ok": False, "result": f"No streaming links found for {title} EP{episode}", "side_effects": []}

    return {"ok": True, "result": _format_links(links["links"], title, f"EP{episode}"), "side_effects": []}


# Register on import
tool_registry.register("find_anime", handle_find_anime, schema={
    "name": "find_anime",
    "description": "Find anime episodes and get streaming URLs. Use when user asks to find, watch, or look up anime. Arg: 'title' for search, or 'title|episode_number' for a specific episode link.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Anime title to search, or 'title|episode_number' for specific episode. Example: 'frieren|9' for Frieren episode 9",
            },
        },
        "required": ["query"],
    },
})
