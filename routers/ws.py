"""WS /ws — WebSocket endpoint for Tab A9+ (Unity VRM app)."""

import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.ws_manager import manager
from services.tools.costume_tool import set_current_costume
from services import sleep
import config

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Persistent WebSocket connection for the VRM display client.

    The client (Unity app on Tab A9+) connects here and receives:
    - Chat replies: {"type": "chat", "reply": "...", "emotion": "HAPPY", "audio_url": "/audio/xxx.wav"}
    - Events:       {"type": "event", "source": "camera", "event": "motion_detected", ...}
    - Commands:     {"type": "command", "action": "idle_talk", ...}

    The client can also send messages back (e.g. touch events, status pings).
    """
    await manager.connect(ws)

    await manager.send_to(ws, {
        "type": "connected",
        "message": "Connected to Avatar AI Server",
        "clients": manager.client_count,
    })

    # Send current sleep state so reconnecting clients sync immediately
    if sleep.is_sleeping():
        await manager.send_to(ws, {"type": "sleep", "sleeping": True})

    # Send current touch config so tablet syncs on reconnect
    await manager.send_to(ws, {"type": "config", "touch_enabled": config.TOUCH_ENABLED})

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "unknown")

            if msg_type == "ping":
                await manager.send_to(ws, {"type": "pong"})

            elif msg_type == "status":
                await manager.send_to(ws, {
                    "type": "status",
                    "clients": manager.client_count,
                })

            elif msg_type == "costume_report":
                costume_id = data.get("costume_id", "")
                if costume_id:
                    set_current_costume(costume_id)
                    logger.info(f"[ws] Client reported costume: {costume_id}")

            elif msg_type == "thr_result":
                item_id = data.get("item_id", "")
                if item_id:
                    from services.tools.thr_tool import handle_thr_result
                    await handle_thr_result(item_id)
                    logger.info(f"[ws] THR result received: item_id={item_id}")

            else:
                logger.info(f"[ws] Received from client: {msg_type} — {data}")

    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        logger.warning(f"[ws] Connection error: {e}")
        manager.disconnect(ws)
