"""Prayer plugin — Islamic prayer time reminders via Aladhan API.

Uses background_task with run_during_sleep=True:
- Awake: enqueues LLM prompt for spoken reminder
- Sleeping: sends silent Telegram push only
"""

import logging
from datetime import timezone, timedelta
from services import prayer, sleep, ntfy
from services.message_queue import QueueItem, enqueue
import config

logger = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))


async def _speak(prompt: str, context: str):
    """Run prompt through LLM → TTS → broadcast (imported from background pattern)."""
    from services import llm, tts_service, memory, sticker, mood
    from services.ws_manager import manager
    import asyncio
    import random

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


async def background_tick():
    """Called every 60s by the background loop (runs during sleep too)."""
    try:
        due = await prayer.get_due_reminders()
    except Exception:
        return

    is_sleeping = sleep.is_sleeping()

    for r in due:
        name = r["name"]
        time_str = r["time"]
        minutes = r["minutes_until"]

        if is_sleeping:
            # Silent Telegram push only
            await ntfy.notify(
                title=f"Prayer: {name}",
                message=f"{time_str} WIB — {'now' if minutes <= 0 else f'in {minutes} min'}",
                priority=4,
            )
        else:
            # Full LLM + TTS reminder
            if minutes <= 0:
                prompt = f"It's time for {name} prayer ({time_str} WIB). Remind the user — say it naturally. One sentence."
            else:
                prompt = f"{name} prayer is in {minutes} minutes ({time_str} WIB). Give the user a gentle reminder. One sentence."

            await ntfy.notify(
                title=f"Prayer: {name}",
                message=f"{time_str} WIB — {'now' if minutes <= 0 else f'in {minutes} min'}",
                priority=4,
                tags=["prayer"],
            )

            await enqueue(QueueItem(
                type="reminder",
                handler=lambda p=prompt: _speak(p, "prayer_reminder"),
                label=f"prayer: {name} ({minutes}m)",
            ))

            from services import background
            background.reset_idle_timer()


async def cmd_prayer(args: str = "") -> dict:
    """Show next prayer time."""
    try:
        times = await prayer.fetch_prayer_times()
        if not times:
            return {"text": "Prayer times unavailable.", "inline_keyboard": None}

        timings = times["timings"]
        name, time_str, minutes = prayer.get_next_prayer(timings)

        lines = [f"🕌 Prayer Times ({times.get('location', 'Unknown')})"]
        for pname in ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]:
            t = timings.get(pname, "?")
            marker = " ◄" if pname == name else ""
            lines.append(f"  {pname}: {t}{marker}")

        if name and minutes is not None:
            lines.append(f"\nNext: {name} in {minutes} min")

        return {"text": "\n".join(lines), "inline_keyboard": None}
    except Exception as e:
        return {"text": f"Error: {e}", "inline_keyboard": None}
