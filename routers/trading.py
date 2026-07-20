"""POST /trading/announce — speak an EXACT string (no LLM).

Used by the SMC trading bot (separate container) to have the character voice
trade events verbatim: order opened, trade closed, start/stop, errors. Routed
through the serialized message queue so it never collides with chat TTS.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

import config
from services import ntfy, tts_service
from services.message_queue import QueueItem, enqueue
from services.ws_manager import manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/trading")


class AnnounceRequest(BaseModel):
    text: str
    emotion: Optional[str] = "neutral"


async def _speak_exact(text: str, emotion: str):
    """Synthesize the exact text (no LLM) and broadcast to the Tab, mirroring
    the chat payload so it renders identically to normal speech."""
    audio_url = None
    if config.TTS_ENABLED and tts_service.is_ready() and manager.client_count > 0:
        audio_url = await asyncio.to_thread(
            tts_service.synthesize, text, None, 1.0, emotion.lower()
        )
    if manager.client_count > 0:
        await manager.broadcast({
            "type": "chat",
            "reply": text,
            "emotion": emotion.upper(),
            "audio_url": audio_url,
            "context": "trade",
            "user_name": "System",
        })


@router.post("/announce")
async def post_announce(req: AnnounceRequest):
    """Enqueue an exact-text announcement (high priority, won't be dropped)."""
    text = (req.text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}
    emotion = req.emotion or "neutral"
    logger.info(f"[trading] announce: {text[:80]}")
    # Push to Telegram so the user is informed on their phone even when the
    # tablet avatar isn't connected. Fire-and-forget — never blocks the response.
    asyncio.create_task(ntfy.notify(config.CHARACTER_NAME, text))
    accepted = await enqueue(QueueItem(
        type="reminder",  # high priority — survives a full queue
        handler=lambda t=text, e=emotion: _speak_exact(t, e),
        label=f"trade_announce: {text[:40]}",
    ))
    return {"ok": accepted}
