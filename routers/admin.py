"""Admin endpoints — runtime config, sleep control, memory management."""

import logging
import random
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services import sleep, memory, background, tts_service, sticker, ntfy
from services.ws_manager import manager
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")


class ConfigUpdate(BaseModel):
    sleep_force: Optional[str] = None        # "on" | "off"
    stt_enabled: Optional[bool] = None
    tts_enabled: Optional[bool] = None
    idle_talk_hours: Optional[float] = None
    sticker_chance: Optional[float] = None
    idle_tool_chance: Optional[float] = None
    touch_enabled: Optional[bool] = None
    sleep_hour: Optional[int] = None
    wake_hour: Optional[int] = None
    tts_language: Optional[str] = None  # "en" | "jp"


@router.get("/config")
async def get_config():
    """Return current runtime configuration."""
    return {
        "stt_enabled": config.STT_ENABLED,
        "tts_enabled": config.TTS_ENABLED,
        "idle_talk_hours": config.IDLE_TALK_INTERVAL_HOURS,
        "sticker_chance": config.STICKER_CHANCE,
        "touch_enabled": config.TOUCH_ENABLED,
        "tts_language": config.TTS_LANGUAGE,
        "tts_engine": config.TTS_ENGINE,
        "sleep": sleep.get_status(),
        "memory": {
            "total_messages": memory.get_message_count(),
            "core_memories": memory.get_core_memory_count(),
        },
    }


@router.post("/config")
async def update_config(req: ConfigUpdate):
    """Update runtime configuration. Only provided fields are changed."""
    changes = []

    if req.sleep_force == "on":
        sleep.go_to_sleep("admin")

        # Run goodnight sequence in background so HTTP response returns instantly
        async def _goodnight():
            reply = random.choice([
                "Fine, fine... good night. Don't stay up too late yourself.",
                "Mm... okay, Sui-chan is going to sleep now. Night~",
                "Oyasumi~ Don't miss me too much while I'm asleep.",
                "Alright, alright... Sui-chan needs her beauty sleep anyway.",
                "Going to sleep now... wake me up if something important happens.",
            ])
            emotion = "NEUTRAL"
            memory.add_message("assistant", reply, emotion=emotion)

            audio_url = None
            if config.TTS_ENABLED and tts_service.is_ready():
                audio_url = await asyncio.to_thread(
                    tts_service.synthesize, reply, None, 1.0, emotion.lower()
                )

            if manager.client_count > 0:
                await manager.broadcast({
                    "type": "chat",
                    "reply": reply,
                    "emotion": emotion,
                    "audio_url": audio_url,
                    "context": "admin",
                    "user_name": "System",
                })

            await ntfy.notify(title=config.CHARACTER_NAME, message=reply)
            if config.STICKER_ENABLED:
                sticker_id = sticker.resolve(reply, emotion)
                if sticker_id:
                    await ntfy.send_sticker(sticker_id)

            # Delay sleep broadcast so Unity plays the goodnight voice first
            await asyncio.sleep(4)
            if manager.client_count > 0:
                await manager.broadcast({"type": "sleep", "sleeping": True})

        asyncio.create_task(_goodnight())
        changes.append("sleep: on")
    elif req.sleep_force == "off":
        sleep.wake_up("admin")
        if manager.client_count > 0:
            await manager.broadcast({"type": "sleep", "sleeping": False})
        changes.append("sleep: off")

    if req.stt_enabled is not None:
        config.STT_ENABLED = req.stt_enabled
        changes.append(f"stt_enabled: {req.stt_enabled}")

    if req.tts_enabled is not None:
        config.TTS_ENABLED = req.tts_enabled
        changes.append(f"tts_enabled: {req.tts_enabled}")

    if req.idle_talk_hours is not None:
        config.IDLE_TALK_INTERVAL_HOURS = req.idle_talk_hours
        changes.append(f"idle_talk_hours: {req.idle_talk_hours}")

    if req.sticker_chance is not None:
        config.STICKER_CHANCE = req.sticker_chance
        changes.append(f"sticker_chance: {req.sticker_chance}")

    if req.idle_tool_chance is not None:
        background.IDLE_TOOL_CHANCE = req.idle_tool_chance
        changes.append(f"idle_tool_chance: {req.idle_tool_chance}")

    if req.touch_enabled is not None:
        config.TOUCH_ENABLED = req.touch_enabled
        if manager.client_count > 0:
            await manager.broadcast({"type": "config", "touch_enabled": req.touch_enabled})
        changes.append(f"touch_enabled: {req.touch_enabled}")

    if req.sleep_hour is not None:
        sleep.SLEEP_HOUR = req.sleep_hour
        changes.append(f"sleep_hour: {req.sleep_hour}")

    if req.wake_hour is not None:
        sleep.WAKE_HOUR = req.wake_hour
        changes.append(f"wake_hour: {req.wake_hour}")

    if req.tts_language is not None and req.tts_language in ("en", "jp"):
        config.TTS_LANGUAGE = req.tts_language
        changes.append(f"tts_language: {req.tts_language}")

    logger.info(f"[admin] Config updated: {', '.join(changes) if changes else 'no changes'}")
    return {"ok": True, "changes": changes}


@router.get("/memory/stats")
async def memory_stats():
    """Return memory statistics."""
    core_list = memory.get_core_memories()
    return {
        "total_messages": memory.get_message_count(),
        "core_memories": len(core_list),
        "session": memory.get_current_session_id(),
        "core_memory_list": [
            {"id": m["id"], "category": m["category"], "content": m["content"]}
            for m in core_list
        ],
    }


class MemoryDeleteRequest(BaseModel):
    id: int


class MemorySearchDeleteRequest(BaseModel):
    query: str


@router.post("/memory/delete")
async def memory_delete(req: MemoryDeleteRequest):
    """Delete a single core memory by ID."""
    deleted = memory.delete_core_memory(req.id)
    if deleted:
        logger.info(f"[admin] Deleted core memory #{req.id}")
        return {"ok": True, "deleted_id": req.id}
    return {"ok": False, "error": f"Memory #{req.id} not found"}


@router.post("/memory/search-delete")
async def memory_search_delete(req: MemorySearchDeleteRequest):
    """Search core memories by keyword and delete all matches."""
    deleted = memory.search_and_delete_core_memories(req.query)
    logger.info(f"[admin] Search-deleted {len(deleted)} memories matching '{req.query}'")
    return {
        "ok": True,
        "deleted_count": len(deleted),
        "deleted": [{"id": m["id"], "content": m["content"]} for m in deleted],
    }


@router.post("/memory/clear")
async def memory_clear():
    """Clear conversation history (keeps core memories)."""
    count = memory.get_message_count()
    memory.clear_conversation_history()
    logger.info(f"[admin] Cleared {count} messages")
    return {"ok": True, "cleared": count}
