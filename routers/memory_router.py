"""Memory management endpoints — view/add/delete core memories."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services import memory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory", tags=["memory"])


class CoreMemoryRequest(BaseModel):
    category: str
    content: str
    source: Optional[str] = None


@router.get("/core")
async def get_core_memories(category: str | None = None):
    """List all core memories, optionally filtered by category."""
    return {"memories": memory.get_core_memories(category)}


@router.post("/core")
async def add_core_memory(req: CoreMemoryRequest):
    """Add a new core memory."""
    mem_id = memory.add_core_memory(req.category, req.content, req.source)
    return {"id": mem_id, "status": "created"}


@router.delete("/core/{memory_id}")
async def delete_core_memory(memory_id: int):
    """Delete a core memory by ID."""
    deleted = memory.delete_core_memory(memory_id)
    return {"deleted": deleted}


@router.get("/history")
async def get_history(hours: int = 24, limit: int = 100):
    """Get recent conversation history."""
    messages = memory.get_recent_messages(hours=hours, soft_cap=limit)
    return {"messages": messages, "count": len(messages)}


@router.get("/sessions")
async def get_sessions(days: int = 7):
    """Get recent session summaries."""
    sessions = memory.get_recent_sessions(days)
    return {"sessions": sessions}


@router.get("/stats")
async def get_stats():
    """Get memory statistics."""
    return {
        "total_messages": memory.get_message_count(),
        "current_session": memory.get_current_session_id(),
        "core_memories": len(memory.get_core_memories()),
    }
