"""POST /event — Receive events from Redmi (camera, motion, etc.) and broadcast to WebSocket clients."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, Any
from services.ws_manager import manager
from services import background, ntfy, mood
import config

logger = logging.getLogger(__name__)
router = APIRouter()


class EventRequest(BaseModel):
    source: str
    event: str
    data: Optional[dict[str, Any]] = None


class EventResponse(BaseModel):
    ok: bool
    clients_notified: int


@router.post("/event", response_model=EventResponse)
async def post_event(req: EventRequest):
    """
    Receive an event from the Redmi hub and broadcast it to all WebSocket clients.

    Examples:
      POST /event {"source": "camera", "event": "motion_detected"}
      POST /event {"source": "camera", "event": "emotion_recognized", "data": {"emotion": "happy"}}
    """
    background.reset_idle_timer()

    payload = {
        "type": "event",
        "source": req.source,
        "event": req.event,
        "data": req.data,
    }

    await manager.broadcast(payload)
    clients = manager.client_count

    if req.event == "poke" and config.MOOD_ENABLED:
        mood.on_poke()

    if req.event == "poke" and req.data and req.data.get("line"):
        await ntfy.notify(
            title=f"{config.CHARACTER_NAME} (poked!)",
            message=req.data["line"],
        )
    else:
        await ntfy.notify(
            title=f"Event: {req.event}",
            message=f"Source: {req.source}" + (f"\nData: {req.data}" if req.data else ""),
            priority=3,
            tags=["camera"],
        )

    logger.info(f"[event] {req.source}/{req.event} → {clients} client(s)")
    return EventResponse(ok=True, clients_notified=clients)
