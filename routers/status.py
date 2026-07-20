"""GET /status — server health check."""

import time
from fastapi import APIRouter
from services import llm, tts_service, stt_service, memory, background, message_queue, ntfy, freshrss, calendar_service, prayer, sleep, mood
from services.ws_manager import manager
import config

router = APIRouter()

_start_time = time.time()


@router.get("/status")
async def get_status():
    """Return server health and subsystem status."""
    lm_status = await llm.check_llm()
    telegram_notify_status = await ntfy.check_health()
    freshrss_status = await freshrss.check_health()
    calendar_status = await calendar_service.check_health()
    prayer_status = await prayer.check_health()
    idle_elapsed = int(time.time() - background._last_user_interaction) if background._last_user_interaction else 0

    return {
        "server": "ok",
        "character_name": config.CHARACTER_NAME,
        "llm": lm_status,
        "llm_provider": config.LLM_PROVIDER,
        "tts": "disabled" if not config.TTS_ENABLED else ("ok" if tts_service.is_ready() else "error"),
        "stt": "disabled" if not config.STT_ENABLED else ("ok" if stt_service.is_ready() else "error"),
        "telegram_notify": telegram_notify_status,
        "freshrss": freshrss_status,
        "calendar": calendar_status,
        "prayer_times": prayer_status,
        "websocket_clients": manager.client_count,
        "uptime_seconds": int(time.time() - _start_time),
        "sleep": sleep.get_status(),
        "mood": mood.get_status(),
        "memory": {
            "total_messages": memory.get_message_count(),
            "core_memories": len(memory.get_core_memories()),
            "session": memory.get_current_session_id(),
        },
    }
