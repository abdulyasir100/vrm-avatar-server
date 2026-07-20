"""GET /stickers — sticker list endpoint for debugging/management."""

from fastapi import APIRouter
from services import sticker

router = APIRouter()


@router.get("/stickers")
async def get_stickers():
    """Return all loaded stickers."""
    return sticker.get_all()
