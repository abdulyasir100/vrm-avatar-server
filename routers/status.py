"""GET /status — server health check."""

import time
from fastapi import APIRouter
from services import llm, tts_service, stt_service, memory, background, message_queue, ntfy, freshrss, calendar_service, prayer, sleep, mood, sensor
from services.ws_manager import manager
import config

router = APIRouter()

_start_time = time.time()


def _sensor_status() -> dict:
    latest = sensor.get_latest()
    if not latest:
        return {"status": "no_data"}
    st = latest.get("screen_time", {})
    return {
        "status": "ok",
        "last_report": latest.get("_timestamp"),
        "steps": latest.get("steps", 0),
        "screen_time_total_min": sum(st.values()),
    }


@router.get("/status")
async def get_status():
    """Return server health and subsystem status."""
    lm_status = await llm.check_llm()
    tm_status = await background.check_task_manager()
    mm_status = await background.check_money_manager()
    ct_status = await background.check_calorie_tracker()
    telegram_notify_status = await ntfy.check_health()
    freshrss_status = await freshrss.check_health()
    calendar_status = await calendar_service.check_health()
    prayer_status = await prayer.check_health()
    idle_elapsed = int(time.time() - background._last_user_interaction) if background._last_user_interaction else 0

    return {
        "server": "ok",
        "character_name": config.CHARACTER_NAME,
        "character_nicknames": config.CHARACTER_NICKNAMES,
        "llm": lm_status,
        "llm_provider": config.LLM_PROVIDER,
        "llm_fallbacks": [fb["provider"] for fb in config.get_llm_fallback_chain()],
        "task_manager": tm_status,
        "money_manager": mm_status,
        "calorie_tracker": ct_status,
        "telegram_notify": telegram_notify_status,
        "freshrss": freshrss_status,
        "calendar": calendar_status,
        "prayer_times": prayer_status,
        "tts": "disabled" if not config.TTS_ENABLED else ("ok" if tts_service.is_ready() else "error"),
        "stt": "disabled" if not config.STT_ENABLED else ("ok" if stt_service.is_ready() else "error"),
        "websocket_clients": manager.client_count,
        "uptime_seconds": int(time.time() - _start_time),
        "queue_size": message_queue.queue_size(),
        "queue_busy": message_queue.is_busy(),
        "idle_seconds_since_interaction": idle_elapsed,
        "idle_talk_threshold_hours": config.IDLE_TALK_INTERVAL_HOURS,
        "sleep": sleep.get_status(),
        "mood": mood.get_status(),
        "sensor": _sensor_status(),
        "memory": {
            "total_messages": memory.get_message_count(),
            "core_memories": len(memory.get_core_memories()),
            "session": memory.get_current_session_id(),
        },
    }
