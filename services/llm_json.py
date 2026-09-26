"""One-shot "answer with a JSON object" calls for background machinery (not chat turns).

Tries the Claude CLI when it is the configured provider, then every OpenAI-compatible
endpoint in the fallback chain, so callers work on any rung of the LLM ladder.
"""

import asyncio
import json
import logging
import re

from openai import OpenAI

import config
from services.claude_cli import query_claude_cli

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str | None) -> dict | None:
    """The JSON object in `text`, tolerating markdown fences and surrounding prose."""
    if not text:
        return None
    cleaned = _FENCE.sub("", text.strip())
    for candidate in (cleaned, cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _endpoints() -> list[dict]:
    chain = []
    if config.LLM_PROVIDER != "claude":
        chain.append({"base_url": config.get_llm_base_url(), "api_key": config.get_llm_api_key() or "no-key",
                      "model": config.get_llm_model()})
    chain += [fb for fb in config.get_llm_fallback_chain() if fb.get("api_key")]
    return chain


async def ask_json(system: str | None, user: str, *, temperature: float = 0.0, timeout: int = 60,
                   max_tokens: int = 2500) -> dict | None:
    """Ask for one JSON object. None when no rung produced a parseable one.
    An empty `system` sends the user message alone."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    if config.LLM_PROVIDER == "claude":
        try:
            parsed = parse_json_object(await query_claude_cli(messages=messages, model="haiku", effort="low",
                                                              timeout=timeout))
            if parsed is not None:
                return parsed
        except Exception as e:
            logger.warning(f"[llm_json] Claude CLI failed: {e!r}")

    for endpoint in _endpoints():
        try:
            client = OpenAI(base_url=endpoint["base_url"], api_key=endpoint["api_key"], timeout=timeout)
            response = await asyncio.wait_for(
                asyncio.to_thread(client.chat.completions.create, model=endpoint["model"], messages=messages,
                                  temperature=temperature, max_tokens=max_tokens),
                timeout=timeout + 5)
            parsed = parse_json_object(response.choices[0].message.content)
            if parsed is not None:
                return parsed
        except Exception as e:
            logger.warning(f"[llm_json] {endpoint['model']} failed: {e!r}")
    return None
