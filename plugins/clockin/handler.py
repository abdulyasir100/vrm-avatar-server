"""Clock-in plugin — auto clock-in/clock-out via Playwright.

Uses background_task (60s tick) to check scheduling:
- 00:01 WIB Mon-Fri: pick random clock-in time between 07:30-08:00
- At scheduled time: run Playwright clock-in, set clock-out = clock_in + 9h
- At clock-out time: run Playwright clock-out
- Notifications via LLM + TTS (same pattern as prayer plugin)
"""

import logging
import os
import random
from datetime import datetime, timezone, timedelta

from services import sleep, ntfy
from services.message_queue import QueueItem, enqueue
import config

logger = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))

# Schedule config from env
CLOCKIN_EARLIEST = os.environ.get("CLOCKIN_EARLIEST", "07:30")
CLOCKIN_LATEST = os.environ.get("CLOCKIN_LATEST", "08:00")
CLOCKOUT_OFFSET_HOURS = int(os.environ.get("CLOCKOUT_OFFSET_HOURS", "9"))

_storage = None


def set_storage(s):
    global _storage
    _storage = s


async def _speak(prompt: str, context: str):
    """Run prompt through LLM -> TTS -> broadcast."""
    from services import llm, tts_service, memory, sticker
    from services.ws_manager import manager
    import asyncio

    lm_status = await llm.check_llm()
    if lm_status != "ok":
        return

    result = await llm.chat(message=prompt, context=context, user_name="System")
    reply = result.get("reply", "")
    emotion = result.get("emotion", config.DEFAULT_EMOTION)

    if not reply or reply == config.OFFLINE_REPLY:
        return

    memory.add_message("assistant", reply, emotion=emotion)

    audio_url = None
    if config.TTS_ENABLED and tts_service.is_ready() and manager.client_count > 0:
        audio_url = await asyncio.to_thread(
            tts_service.synthesize, reply, None, 1.0, emotion.lower()
        )

    if manager.client_count > 0:
        await manager.broadcast({
            "type": "chat",
            "reply": reply,
            "emotion": emotion,
            "audio_url": audio_url,
            "context": context,
            "user_name": "System",
        })

    await ntfy.notify(title=config.CHARACTER_NAME, message=reply)

    if config.STICKER_ENABLED and random.random() < config.STICKER_CHANCE:
        from services import sticker as sticker_svc
        sticker_id = sticker_svc.resolve(reply, emotion)
        if sticker_id:
            await ntfy.send_sticker(sticker_id)

    chat_logger = logging.getLogger("chat")
    chat_logger.info(f"[{context}] {config.CHARACTER_NAME} [{emotion}]: {reply}")


def _now_wib() -> datetime:
    return datetime.now(WIB)


def _random_clockin_time() -> str:
    """Pick random HH:MM between CLOCKIN_EARLIEST and CLOCKIN_LATEST."""
    h_early, m_early = map(int, CLOCKIN_EARLIEST.split(":"))
    h_late, m_late = map(int, CLOCKIN_LATEST.split(":"))

    earliest_min = h_early * 60 + m_early
    latest_min = h_late * 60 + m_late
    picked = random.randint(earliest_min, latest_min)

    return f"{picked // 60:02d}:{picked % 60:02d}"


async def background_tick():
    """Called every 60s by the background loop."""
    if not _storage or not _storage.is_enabled():
        return

    now = _now_wib()

    # Skip weekends
    if now.weekday() >= 5:
        return

    state = _storage.get_today_state()
    hhmm = now.strftime("%H:%M")

    # Phase 1: Schedule today's clock-in (once per day, early morning)
    if not state.get("scheduled_clockin"):
        if now.hour < 1 or (now.hour == 0 and now.minute <= 5):
            # Too early, wait for 00:01
            return
        clockin_time = _random_clockin_time()
        state["scheduled_clockin"] = clockin_time
        clockout_h, clockout_m = map(int, clockin_time.split(":"))
        clockout_total = clockout_h * 60 + clockout_m + CLOCKOUT_OFFSET_HOURS * 60 + 1
        state["scheduled_clockout"] = f"{clockout_total // 60:02d}:{clockout_total % 60:02d}"
        _storage.save_today_state(state)
        logger.info(f"[clockin] Scheduled: in={clockin_time}, out={state['scheduled_clockout']}")
        return

    # Phase 2: Execute clock-in
    if not state.get("clockin_done") and hhmm >= state["scheduled_clockin"]:
        logger.info(f"[clockin] Executing clock-in (scheduled: {state['scheduled_clockin']}, now: {hhmm})")

        from plugins.clockin.clocker import clock_in
        result = await clock_in()

        state["clockin_done"] = result["success"]
        state["clockin_time"] = hhmm
        state["clockin_result"] = result["message"]
        _storage.save_today_state(state)

        if result["success"]:
            prompt = (
                f"[SYSTEM] You just clocked in for work at {hhmm} WIB. "
                f"Clock-out is scheduled at {state['scheduled_clockout']} WIB. "
                f"Announce this naturally in one sentence."
            )
            await enqueue(QueueItem(
                type="clockin",
                handler=lambda p=prompt: _speak(p, "clockin"),
                label=f"clockin: {hhmm}",
            ))
            msg_id = await ntfy.notify(
                title="Clock-in",
                message=f"Clocked in at {hhmm} WIB. Out at {state['scheduled_clockout']}.",
            )
            if msg_id:
                await ntfy.pin_message(msg_id)
        else:
            await ntfy.notify(
                title="Clock-in FAILED",
                message=result["message"],
                priority=5,
            )
        return

    # Phase 3: Execute clock-out
    if (
        state.get("clockin_done")
        and not state.get("clockout_done")
        and state.get("scheduled_clockout")
        and hhmm >= state["scheduled_clockout"]
    ):
        logger.info(f"[clockin] Executing clock-out (scheduled: {state['scheduled_clockout']}, now: {hhmm})")

        from plugins.clockin.clocker import clock_out
        result = await clock_out()

        state["clockout_done"] = result["success"]
        state["clockout_time"] = hhmm
        state["clockout_result"] = result["message"]
        _storage.save_today_state(state)

        if result["success"]:
            prompt = (
                f"[SYSTEM] You just clocked out from work at {hhmm} WIB. "
                f"Announce this naturally in one sentence — the workday is over."
            )
            await enqueue(QueueItem(
                type="clockout",
                handler=lambda p=prompt: _speak(p, "clockout"),
                label=f"clockout: {hhmm}",
            ))
            await ntfy.notify(
                title="Clock-out",
                message=f"Clocked out at {hhmm} WIB.",
            )
        else:
            await ntfy.notify(
                title="Clock-out FAILED",
                message=result["message"],
                priority=5,
            )


async def cmd_clockin(args: str = "") -> dict:
    """Toggle clock-in automation on/off."""
    if not _storage:
        return {"text": "Clock-in storage not initialized.", "inline_keyboard": None}

    if args in ("on", "off"):
        enabled = args == "on"
        _storage.set_enabled(enabled)
        return {"text": f"Clock-in automation {'enabled' if enabled else 'disabled'}.", "inline_keyboard": None}

    return {"text": "Usage: /p.clockin on|off", "inline_keyboard": None}


async def cmd_status(args: str = "") -> dict:
    """Show today's clock-in schedule."""
    if not _storage:
        return {"text": "Clock-in storage not initialized.", "inline_keyboard": None}

    enabled = _storage.is_enabled()
    state = _storage.get_today_state()
    now = _now_wib()

    lines = [
        f"Clock-in: {'ON' if enabled else 'OFF'}",
        f"Time: {now.strftime('%H:%M WIB')} ({now.strftime('%A')})",
        "",
        f"Today ({state.get('date', 'N/A')}):",
        f"  Scheduled in: {state.get('scheduled_clockin', '-')}",
        f"  Scheduled out: {state.get('scheduled_clockout', '-')}",
        f"  Clock-in: {state.get('clockin_time', 'pending')}" + (" OK" if state.get('clockin_done') else ""),
        f"  Clock-out: {state.get('clockout_time', 'pending')}" + (" OK" if state.get('clockout_done') else ""),
    ]

    if state.get("clockin_result") and not state.get("clockin_done"):
        lines.append(f"  Error: {state['clockin_result']}")

    return {"text": "\n".join(lines), "inline_keyboard": None}


async def cmd_test(args: str = "") -> dict:
    """Test login + GPS detection without clocking in."""
    from plugins.clockin.clocker import test_login

    result = await test_login()

    if result["success"]:
        coords = result.get("coords")
        if coords:
            lines = [
                "Test OK",
                f"  GPS: {coords['lat']:.6f}, {coords['lng']:.6f}",
                f"  Screenshot: {result.get('screenshot', 'N/A')}",
            ]
        else:
            lines = [
                "Test OK (login + presences)",
                "  GPS: could not read (page may not expose it)",
                f"  Screenshot: {result.get('screenshot', 'N/A')}",
            ]
    else:
        lines = [
            f"Test FAILED: {result['message']}",
            f"  Screenshot: {result.get('screenshot', 'N/A')}",
        ]

    return {"text": "\n".join(lines), "inline_keyboard": None}
