"""Background task reminder poller — checks Task Manager for upcoming reminders
and broadcasts them via WebSocket + optionally triggers Suisei voice alerts."""

import asyncio
import logging
import httpx
from services.ws_manager import manager
import config

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_seen: set[str] = set()


async def _poll_loop():
    """Periodically check for upcoming task reminders."""
    interval = config.TASK_REMINDER_POLL_SECONDS
    api_url = config.TASK_MANAGER_URL

    logger.info(f"[task_reminder] Poller started (every {interval}s, API: {api_url})")

    while True:
        try:
            await asyncio.sleep(interval)
            await _check_reminders(api_url)
        except asyncio.CancelledError:
            logger.info("[task_reminder] Poller stopped")
            break
        except Exception as e:
            logger.error(f"[task_reminder] Poll error: {e}")


async def _check_reminders(api_url: str):
    """Fetch upcoming reminders and broadcast new ones."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{api_url}/tasks/reminders/upcoming", params={"within_minutes": 15})
            if resp.status_code != 200:
                return
            reminders = resp.json()
    except Exception:
        return

    for r in reminders:
        key = f"{r['task_id']}-{r['minutes_until_due']}"
        if key in _seen:
            continue
        _seen.add(key)

        title = r["title"]
        minutes = r["minutes_until_due"]
        logger.info(f"[task_reminder] Reminder: \"{title}\" due in {minutes} min")

        # Broadcast to WebSocket clients (Tablet)
        if manager.client_count > 0:
            await manager.broadcast({
                "type": "reminder",
                "title": title,
                "minutes_until_due": minutes,
                "task_id": r["task_id"],
            })

    if len(_seen) > 200:
        _seen.clear()


async def check_task_manager() -> str:
    """Health check — is the Task Manager reachable?"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.TASK_MANAGER_URL}/health")
            if resp.status_code == 200:
                return "ok"
    except Exception:
        pass
    return "offline"


def start():
    """Start the background reminder poller."""
    global _task
    if _task is not None:
        return
    _task = asyncio.create_task(_poll_loop())


def stop():
    """Stop the background reminder poller."""
    global _task
    if _task:
        _task.cancel()
        _task = None
