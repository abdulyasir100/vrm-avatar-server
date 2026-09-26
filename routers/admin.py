"""Admin endpoints — runtime config, sleep control, memory management."""

import logging
import random
import asyncio
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from services import sleep, memory, tts_service, sticker, ntfy
from services import runtime_settings, settings_registry as registry
from services.auth import require_key
from services.ws_manager import manager
import config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", dependencies=[Depends(require_key("ADMIN_KEY", "X-Admin-Key"))])


@router.get("/config")
async def get_config():
    """Return current runtime configuration.

    Flat keys are kept for older clients; `schema` (from settings_registry) is the
    complete, self-describing list the Telegram bot renders.
    """
    return {
        "stt_enabled": config.STT_ENABLED,
        "tts_enabled": config.TTS_ENABLED,
        "idle_talk_hours": config.IDLE_TALK_INTERVAL_HOURS,
        "sticker_chance": config.STICKER_CHANCE,
        "touch_enabled": config.TOUCH_ENABLED,
        "tts_language": config.TTS_LANGUAGE,
        "tts_engine": config.TTS_ENGINE,
        "tool_call_mode": config.TOOL_CALL_MODE,
        "sleep": sleep.get_status(),
        "memory": {
            "total_messages": memory.get_message_count(),
            "core_memories": memory.get_core_memory_count(),
        },
        "schema": registry.schema(runtime_settings.overridden_keys()),
    }


async def _goodnight():
    """Goodnight line + TTS + broadcast, run in background so the HTTP reply is instant."""
    reply = random.choice([
        "Fine, fine... good night. Don't stay up too late yourself.",
        "Mm... okay, I'm going to sleep now. Night~",
        "Oyasumi~ Don't miss me too much while I'm asleep.",
        "Alright, alright... I need my beauty sleep anyway.",
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


@router.post("/config")
async def update_config(body: dict = Body(...)):
    """Update runtime configuration. Only provided keys change.

    `sleep_force` ("on"|"off") is an action; every other key must be a registered
    setting. All values are validated before anything changes — one bad key rejects
    the whole request with HTTP 400.
    """
    body = dict(body)
    sleep_force = body.pop("sleep_force", None)

    values, errors = {}, []
    for key, raw in body.items():
        if raw is None:
            continue
        try:
            values[key] = registry.coerce(key, raw)
        except ValueError as e:
            errors.append(str(e))
    if sleep_force not in (None, "on", "off"):
        errors.append("sleep_force must be on|off")
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    changes = []
    if sleep_force == "on":
        sleep.go_to_sleep("admin")
        asyncio.create_task(_goodnight())
        changes.append("sleep: on")
    elif sleep_force == "off":
        sleep.wake_up("admin")
        if manager.client_count > 0:
            await manager.broadcast({"type": "sleep", "sleeping": False})
        changes.append("sleep: off")

    for key, value in values.items():
        await registry.change(key, value)
        changes.append(f"{key}: {value}")

    # Persist the changed values so they survive container rebuilds/deploys.
    runtime_settings.save(values)

    logger.info(f"[admin] Config updated: {', '.join(changes) if changes else 'no changes'}")
    return {"ok": True, "changes": changes, "values": values}


@router.delete("/config/{key}")
async def reset_config(key: str):
    """Drop a setting's override and restore its code/env default."""
    if key not in registry.REGISTRY:
        raise HTTPException(status_code=400, detail=f"unknown setting '{key}'")
    value = registry.default(key)
    await registry.change(key, value)
    runtime_settings.reset(key)
    logger.info(f"[admin] Config reset: {key} -> {value}")
    return {"ok": True, "changes": [f"{key}: {value} (default)"], "values": {key: value}}


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


@router.get("/user-state")
async def user_state_get():
    """Return current cached user state snapshot."""
    from services import user_state
    return user_state.get_state()


@router.post("/user-state/refresh")
async def user_state_refresh():
    """Force an immediate re-derivation of user state."""
    from services import user_state
    new_state = await user_state.derive_now()
    if new_state is None:
        return {"ok": False, "error": "derivation failed; state unchanged"}
    return {"ok": True, "state": new_state}
