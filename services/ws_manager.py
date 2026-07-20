"""WebSocket connection manager — tracks connected clients (Tab A9+, etc.)."""

import logging
import json
from typing import Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts messages to all clients."""

    def __init__(self):
        self._connections: list[WebSocket] = []

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        logger.info(f"[ws] Client connected — total: {self.client_count}")

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info(f"[ws] Client disconnected — total: {self.client_count}")

    async def broadcast(self, data: dict[str, Any]):
        """Send JSON data to all connected clients."""
        if not self._connections:
            return

        message = json.dumps(data)
        stale: list[WebSocket] = []

        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.disconnect(ws)

        if self._connections:
            logger.debug(f"[ws] Broadcast to {len(self._connections)} client(s)")

    async def ping_all(self):
        """Send a lightweight ping to all clients to keep connections alive."""
        if not self._connections:
            return

        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(json.dumps({"type": "pong"}))
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.disconnect(ws)

    async def send_to(self, ws: WebSocket, data: dict[str, Any]):
        """Send JSON data to a specific client."""
        try:
            await ws.send_text(json.dumps(data))
        except Exception:
            self.disconnect(ws)


manager = ConnectionManager()
