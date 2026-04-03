"""POST /costume — Switch VRM costume on the Tab A9+ avatar display."""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.ws_manager import manager
from services import costume_registry

logger = logging.getLogger(__name__)
router = APIRouter()


class CostumeRequest(BaseModel):
    costume_id: str


class CostumeResponse(BaseModel):
    ok: bool
    costume_id: str
    clients_notified: int


@router.post("/costume", response_model=CostumeResponse)
async def switch_costume(req: CostumeRequest):
    """Switch the VRM avatar costume on connected clients."""
    costume_id = costume_registry.resolve(req.costume_id)

    if not costume_id:
        available = sorted(costume_registry.get_enabled().keys())
        raise HTTPException(
            status_code=400,
            detail=f"No costume matches '{req.costume_id}'. Available: {available}",
        )

    costume_registry.set_current(costume_id)

    payload = {
        "type": "costume",
        "costume_id": costume_id,
    }

    await manager.broadcast(payload)
    clients = manager.client_count

    logger.info(f"[costume] Switching to '{costume_id}' → {clients} client(s)")
    return CostumeResponse(ok=True, costume_id=costume_id, clients_notified=clients)


@router.get("/costumes")
async def list_costumes():
    """Return available costumes with tags."""
    return {"costumes": costume_registry.get_enabled()}
