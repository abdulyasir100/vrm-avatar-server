"""Sleep mode — character's sleep/wake cycle.

Schedule:
  - Sleeps at 00:00 WIB (midnight) or on user command
  - Wakes at 05:00 WIB or when user sends a chat
  - If woken early by chat: stays awake for FORCED_WAKE_MINUTES, then back to sleep

When sleeping:
  - No idle talk, no background LLM calls
  - Prayer/calendar reminders still push to Telegram (no voice)
  - Chat messages wake her up (groggy/annoyed responses)
"""

import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

SLEEP_HOUR = 0       # midnight
WAKE_HOUR = 5        # 5 AM
FORCED_WAKE_MINUTES = 5  # stay awake this long when user wakes her

_is_sleeping: bool = False
_sleep_start: float = 0.0
_forced_wake_until: float = 0.0


def _now_wib() -> datetime:
    return datetime.now(WIB)


def _is_sleep_hours() -> bool:
    """Check if current time is within sleep window (00:00 - 05:00 WIB)."""
    hour = _now_wib().hour
    return hour >= SLEEP_HOUR and hour < WAKE_HOUR  # 0 <= hour < 5


def is_sleeping() -> bool:
    """Check if character is currently sleeping. Handles auto-sleep/wake transitions."""
    global _is_sleeping

    in_sleep_window = _is_sleep_hours()

    if _is_sleeping and not in_sleep_window:
        wake_up("scheduled")
        return False

    if not _is_sleeping and in_sleep_window:
        if time.time() > _forced_wake_until:
            go_to_sleep("scheduled")
            return True

    return _is_sleeping


async def _send_sleep_sticker() -> None:
    """Send Zzz sticker when going to sleep (best-effort)."""
    try:
        from services import sticker, ntfy
        import config
        if config.STICKER_ENABLED:
            file_id = sticker.get_sticker("SLEEP")
            if file_id:
                await ntfy.send_sticker(file_id)
    except Exception:
        pass


def go_to_sleep(reason: str = "manual") -> None:
    """Put character to sleep."""
    global _is_sleeping, _sleep_start, _forced_wake_until
    if _is_sleeping:
        return
    _is_sleeping = True
    _sleep_start = time.time()
    _forced_wake_until = 0.0
    logger.info(f"[sleep] Fell asleep ({reason})")

    # Send Zzz sticker (fire-and-forget)
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(_send_sleep_sticker())
    except RuntimeError:
        pass  # No running loop (e.g. during tests)


def wake_up(reason: str = "manual") -> bool:
    """Wake character up. Returns True if she was actually sleeping."""
    global _is_sleeping, _forced_wake_until
    was_sleeping = _is_sleeping
    _is_sleeping = False

    now = _now_wib()
    hour = now.hour

    if was_sleeping and (hour >= SLEEP_HOUR and hour < WAKE_HOUR):
        _forced_wake_until = time.time() + (FORCED_WAKE_MINUTES * 60)
        logger.info(f"[sleep] Woke up ({reason}) — forced wake for {FORCED_WAKE_MINUTES}min")
    else:
        _forced_wake_until = 0.0
        logger.info(f"[sleep] Woke up ({reason})")

    return was_sleeping


def is_forced_wake() -> bool:
    """Is she temporarily awake during sleep hours?"""
    return _forced_wake_until > 0 and time.time() < _forced_wake_until


def get_status() -> dict:
    """Return sleep status for /status endpoint."""
    now = _now_wib()
    return {
        "is_sleeping": _is_sleeping,
        "current_hour_wib": now.hour,
        "sleep_schedule": f"{SLEEP_HOUR:02d}:00-{WAKE_HOUR:02d}:00 WIB",
        "forced_wake": is_forced_wake(),
        "forced_wake_minutes_left": max(0, int((_forced_wake_until - time.time()) / 60)) if is_forced_wake() else 0,
    }
