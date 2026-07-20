"""Serialized message queue — all TTS + WebSocket broadcasts go through here.

Prevents concurrent TTS calls (VRAM contention) and audio overlap on Unity.

Pipeline split:
  - LLM response: runs immediately, user always gets text reply via HTTP
  - TTS + broadcast: queued here, processed one at a time

Rules:
  - Max 3 items (1 processing + 2 waiting)
  - Processed sequentially, one at a time
  - When queue is full:
    - Background (reminder/idle): kicks the last waiting item
    - Chat: gets dropped (user already has text reply, the character just won't voice it)
  - Currently processing item is never interrupted
"""

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 3  # 1 processing + 2 waiting


@dataclass
class QueueItem:
    type: str
    handler: Callable[[], Awaitable[Any]]
    label: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def is_background(self) -> bool:
        return self.type in ("reminder", "idle_talk")


_queue: list[QueueItem] = []
_lock = asyncio.Lock()
_processing = False
_worker_event = asyncio.Event()


async def enqueue(item: QueueItem) -> bool:
    """Add an item to the queue.

    Returns True if accepted, False if dropped.
    """
    async with _lock:
        waiting_start = 1 if _processing else 0
        waiting_count = len(_queue) - waiting_start

        if waiting_count >= (MAX_QUEUE_SIZE - (1 if _processing else 0)):
            if item.is_background:
                if len(_queue) > waiting_start:
                    kicked = _queue.pop()
                    logger.info(f"[queue] Kicked: {kicked.type} ({kicked.label}) — replaced by {item.type} ({item.label})")
                else:
                    logger.info(f"[queue] Dropped: {item.type} ({item.label}) — nothing to kick")
                    return False
            elif item.type == "scheduled":
                # Scheduled actions (e.g. the nightly game dev session) are
                # time-critical and must NOT be dropped by a queue full of
                # background nags. At 21:00 the habit reminders fire alongside
                # the dev session and were starving it out. Kick the last
                # WAITING background item (reminder/idle_talk) to make room;
                # only drop if nothing droppable is waiting.
                kick_idx = next(
                    (i for i in range(len(_queue) - 1, waiting_start - 1, -1)
                     if _queue[i].is_background),
                    None,
                )
                if kick_idx is not None:
                    kicked = _queue.pop(kick_idx)
                    logger.info(f"[queue] Kicked: {kicked.type} ({kicked.label}) — made room for scheduled ({item.label})")
                else:
                    logger.info(f"[queue] Dropped: scheduled ({item.label}) — queue full, no background item to kick")
                    return False
            else:
                logger.info(f"[queue] Dropped TTS: {item.type} ({item.label}) — queue full, text reply already sent")
                return False

        _queue.append(item)
        logger.info(f"[queue] Enqueued: {item.type} ({item.label}) — queue: {len(_queue)}")

    _worker_event.set()
    return True


async def _worker():
    """Process queue items one at a time."""
    global _processing

    logger.info("[queue] Worker started")

    while True:
        await _worker_event.wait()

        while True:
            async with _lock:
                if not _queue:
                    _worker_event.clear()
                    _processing = False
                    break
                item = _queue[0]
                _processing = True

            try:
                logger.info(f"[queue] Processing: {item.type} ({item.label})")
                await item.handler()
            except Exception as e:
                logger.error(f"[queue] Error: {item.type} ({item.label}): {e}")

            async with _lock:
                if _queue and _queue[0] is item:
                    _queue.pop(0)

            await asyncio.sleep(0.5)


def queue_size() -> int:
    return len(_queue)


def is_busy() -> bool:
    return _processing


_worker_task: asyncio.Task | None = None


def start():
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.create_task(_worker())


def stop():
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        _worker_task = None
