"""Telegram push notification service — best-effort delivery to user's phone.

Sends messages directly via Telegram Bot API. Replaces Ntfy.
Rate-limited to avoid hitting Telegram's per-chat flood limits.
"""

import asyncio
import logging
import time
import httpx
import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_STICKER_API = "https://api.telegram.org/bot{token}/sendSticker"
_PIN_API = "https://api.telegram.org/bot{token}/pinChatMessage"

# Rate limiter: minimum 1.5s between sends to same chat
_MIN_INTERVAL = 1.5
_last_send_time: float = 0.0
_send_lock = asyncio.Lock()


async def _rate_limit():
    """Wait if needed to respect minimum interval between sends."""
    global _last_send_time
    async with _send_lock:
        now = time.monotonic()
        elapsed = now - _last_send_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_send_time = time.monotonic()


async def notify(
    title: str,
    message: str,
    priority: int = 3,
    tags: list[str] | None = None,
) -> bool:
    """Push a notification via Telegram. Never raises — returns True/False."""
    if not config.TELEGRAM_NOTIFY_ENABLED:
        return False

    # Only show title if it's not just the character name (avoid redundant bot name prefix)
    if title and title != config.CHARACTER_NAME:
        text = f"*{title}*\n{message}"
    else:
        text = message

    url = _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        await _rate_limit()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", {}).get("message_id")
            # Markdown parse error — retry without parse_mode
            if resp.status_code == 400 and "parse entities" in resp.text:
                payload.pop("parse_mode", None)
                resp2 = await client.post(url, json=payload)
                if resp2.status_code == 200:
                    data = resp2.json()
                    return data.get("result", {}).get("message_id")
                logger.warning(f"[telegram_notify] Retry also failed {resp2.status_code}: {resp2.text[:100]}")
            else:
                logger.warning(f"[telegram_notify] POST returned {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"[telegram_notify] Failed: {repr(e)}")
    return None


async def pin_message(message_id: int) -> bool:
    """Pin a message in the Telegram chat. Never raises — returns True/False."""
    if not config.TELEGRAM_NOTIFY_ENABLED or not message_id:
        return False

    url = _PIN_API.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "disable_notification": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning(f"[telegram_pin] Failed {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"[telegram_pin] Failed: {e}")
    return False


async def send_sticker(file_id: str) -> bool:
    """Send a sticker via Telegram Bot API. Never raises — returns True/False."""
    if not config.TELEGRAM_NOTIFY_ENABLED or not file_id:
        return False

    url = _STICKER_API.format(token=config.TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "sticker": file_id,
    }

    try:
        await _rate_limit()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning(f"[telegram_sticker] POST returned {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        logger.warning(f"[telegram_sticker] Failed: {e}")
    return False


async def check_health() -> str:
    """Health check — can we reach the Telegram Bot API?"""
    if not config.TELEGRAM_NOTIFY_ENABLED:
        return "disabled"
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return "ok"
    except Exception:
        pass
    return "offline"
