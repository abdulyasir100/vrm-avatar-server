"""Unified background service — handles task reminders and idle talk.

Both follow the same pattern:
  1. Check a condition (reminder due / idle timeout)
  2. Enqueue a message: LLM → TTS → WebSocket broadcast
  3. Queue handles serialization and prevents VRAM contention
"""

import asyncio
import logging
import time
import random
import httpx
from services import llm, tts_service, memory, weather, ntfy, freshrss, calendar_service, prayer, sleep, mood, sticker, sensor, plugin_loader
from services.ws_manager import manager
from services.message_queue import QueueItem, enqueue
import config

logger = logging.getLogger(__name__)
chat_logger = logging.getLogger("chat")

_task: asyncio.Task | None = None
_seen_reminders: set[str] = set()
_last_user_interaction: float = 0.0
_last_sleep_state: bool | None = None
_last_summarized_at: float = 0.0

def _build_idle_random_prompt() -> str:
    """Build a dynamic idle talk prompt with real-time context for the LLM to decide what to say."""
    from datetime import timezone, timedelta
    wib = timezone(timedelta(hours=7))
    now_wib = __import__("datetime").datetime.now(wib)
    time_str = now_wib.strftime("%H:%M")
    hour = now_wib.hour

    if hour < 6:
        time_of_day = "late night / very early morning"
    elif hour < 10:
        time_of_day = "morning"
    elif hour < 12:
        time_of_day = "late morning"
    elif hour < 14:
        time_of_day = "around noon"
    elif hour < 17:
        time_of_day = "afternoon"
    elif hour < 20:
        time_of_day = "evening"
    else:
        time_of_day = "night"

    idle_minutes = int((time.time() - _last_user_interaction) / 60) if _last_user_interaction else 0

    return (
        f"It's {time_str} WIB ({time_of_day}). "
        f"Your user has been quiet for about {idle_minutes} minutes. "
        "Say something unprompted and in-character. You decide what — "
        "it could be anything: a random thought, teasing the user, "
        "commenting on the time, humming to yourself, complaining about boredom, "
        "bragging about something, sharing an opinion, a dramatic monologue about being ignored, "
        "or whatever feels natural right now. Be creative, don't repeat yourself. "
        "One sentence max."
    )

_IDLE_TOOL_OVERDUE = "The user has {count} overdue task(s): {tasks}. Nag them about it naturally — be in-character (teasing but caring). One sentence."
_IDLE_TOOL_UPCOMING = "The user has these upcoming tasks: {tasks}. Mention one casually — like you're keeping track for them. One sentence."
_IDLE_TOOL_SPENDING = "The user has spent {amount} this month ({percent}% of budget). Comment on their spending casually — not a warning, just an observation. One sentence."
_IDLE_TOOL_NO_TASKS = "The user has no tasks on their to-do list right now. Tease them about being lazy or suggest they add something. One sentence."
_IDLE_WEATHER = "Current weather: {weather}. Make a casual comment about the weather — react naturally as if you looked outside. One sentence."
_IDLE_RSS_GENERAL = 'You just saw this news headline: "{title}" from {source}. Comment on it briefly — react naturally. One sentence.'
def _idle_rss_character_prompt(title: str, source: str) -> str:
    return f'You ({config.CHARACTER_NAME}) just posted something! Title: "{title}" on {source}. Excitedly tell your user about it — like "Hey, I just uploaded a new video!" or "I just tweeted something, check it out~". One sentence.'
_IDLE_CALENDAR = 'The user has an upcoming event: "{title}" at {time}. Mention it casually. One sentence.'
_IDLE_PRAYER = 'Next prayer is {name} at {time} WIB ({minutes} minutes from now). Gently remind the user. One sentence.'
_IDLE_CALORIES = 'The user has eaten {calories} calories today ({percent}% of {target} target). Comment on their eating habits casually. One sentence.'
_IDLE_CALORIES_NONE = 'The user hasn\'t logged any meals today. Tease them about not eating or remind them to log their food. One sentence.'

# Sensor prompts
_SENSOR_SCREEN_GENTLE = 'The user has spent {minutes} minutes on {app} today. Roast them — be savage but funny. One sentence.'
_SENSOR_SCREEN_ANNOYED = 'The user has been on {app} for {minutes} minutes today! Be brutal — call them out hard, no filter. One sentence.'
_SENSOR_SCREEN_ANGRY = 'The user has wasted {minutes} minutes on {app} today!! Go OFF — full savage mode, creative insult, make it sting. One sentence.'
_SENSOR_STEPS_LAZY = 'The user has only walked {steps} steps today and it\'s already evening. Destroy them — call them a lazy fatass or worse. Be creative. One sentence.'
_SENSOR_STEPS_ACTIVE = 'The user has walked {steps} steps today! Reluctantly admit you\'re impressed — but still find something to roast. One sentence.'
_SENSOR_STEPS_PRAISE = 'The user hit {steps} steps today! Actually praise them for once — but make it backhanded. One sentence.'

_SPENDING_PROMPTS = {
    "warning": [
        "The user has spent {percent}% of their monthly budget. Scold them lightly — they're halfway through. Be in-character (teasing, concerned). One sentence.",
        "The user already used {percent}% of their budget this month. Comment on their spending habits with a mix of concern and sass. One sentence.",
    ],
    "critical": [
        "The user has spent {percent}% of their monthly budget! That's way too much. Be genuinely annoyed/angry in-character. Suggest they try saving. One sentence.",
        "The user blew through {percent}% of their budget. Express shock and disappointment in-character. Tell them to stop spending. One sentence.",
    ],
}

_last_spending_nag: str = ""
_step_goal_nagged_date: str = ""
_scheduled_fired_today: dict[str, str] = {}  # "plugin:action_name" -> date fired
_check_fired_today: dict[str, str] = {}  # "plugin-type" -> date fired

# Idle cooldown: track recent idle categories to avoid repeats
# Each entry is the category name, last 4 kept. A category in this list gets skipped.
_IDLE_COOLDOWN_SIZE = 5
_idle_recent: list[str] = []  # e.g. ["sensor", "tool", "random", "smart", "tool"]

def _idle_on_cooldown(category: str) -> bool:
    """Check if this idle category was used in the last N cycles."""
    return category in _idle_recent  # max 1 out of last 5

def _idle_record(category: str):
    """Record that this idle category just fired."""
    _idle_recent.append(category)
    while len(_idle_recent) > _IDLE_COOLDOWN_SIZE:
        _idle_recent.pop(0)

# Persist daily dedup state across restarts
_DAILY_STATE_PATH = "data/background_daily.json"

def _load_daily_state():
    """Load persisted daily dedup state from disk. Resets if date changed."""
    global _step_goal_nagged_date, _scheduled_fired_today, _check_fired_today
    import json
    from pathlib import Path
    from datetime import timezone, timedelta
    today = __import__("datetime").datetime.now(
        timezone(timedelta(hours=7))
    ).strftime("%Y-%m-%d")
    try:
        state = json.loads(Path(_DAILY_STATE_PATH).read_text())
        # Reset if saved date != today
        if state.get("date") != today:
            logger.info(f"[background] Daily state expired (was {state.get('date')}, now {today}), resetting")
            _save_daily_state()
            return
        _step_goal_nagged_date = state.get("step_goal_nagged", "")
        _scheduled_fired_today = state.get("scheduled_fired", {})
        _check_fired_today = state.get("check_fired", {})
        logger.info(f"[background] Loaded daily state: step_goal={_step_goal_nagged_date}, "
                    f"checks={list(_check_fired_today.keys())}")
    except Exception:
        pass

def _save_daily_state():
    """Persist daily dedup state to disk."""
    import json
    from pathlib import Path
    from datetime import timezone, timedelta
    today = __import__("datetime").datetime.now(
        timezone(timedelta(hours=7))
    ).strftime("%Y-%m-%d")
    try:
        Path(_DAILY_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(_DAILY_STATE_PATH).write_text(json.dumps({
            "date": today,
            "step_goal_nagged": _step_goal_nagged_date,
            "scheduled_fired": _scheduled_fired_today,
            "check_fired": _check_fired_today,
        }))
    except Exception:
        pass


def reset_idle_timer():
    """Call this whenever the user interacts (chat, event, etc.)."""
    global _last_user_interaction
    _last_user_interaction = time.time()


async def _speak(prompt: str, context: str):
    """Run a prompt through LLM → TTS → WebSocket broadcast.

    This is called by the queue worker — never concurrently.
    """
    lm_status = await llm.check_llm()
    if lm_status != "ok":
        logger.warning(f"[background] LLM offline, skipping: {prompt[:60]}...")
        return

    result = await llm.chat(message=prompt, context=context, user_name="System", is_system_prompt=True)

    reply = result.get("reply", "")
    emotion = result.get("emotion", config.DEFAULT_EMOTION)

    if not reply or reply == config.OFFLINE_REPLY:
        return

    # Bilingual parsing for JP mode
    tts_text = reply
    if config.TTS_LANGUAGE == "jp" and reply:
        en_text, jp_text = llm.parse_bilingual(reply)
        if jp_text:
            tts_text = jp_text
            reply = en_text

    memory.add_message("assistant", reply, emotion=emotion)

    audio_url = None
    if config.TTS_ENABLED and tts_service.is_ready() and manager.client_count > 0:
        audio_url = await asyncio.to_thread(
            tts_service.synthesize, tts_text, None, 1.0, emotion.lower()
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
        sticker_id = sticker.resolve(reply, emotion)
        if sticker_id:
            await ntfy.send_sticker(sticker_id)

    chat_logger.info(f"[{context}] {config.CHARACTER_NAME} [{emotion}]: {reply}")
    logger.info(f"[background] {config.CHARACTER_NAME} spoke ({context}): [{emotion}] {reply[:80]}...")


async def _check_reminders():
    """Check for upcoming task reminders from plugins and enqueue announcements."""
    reminders = []

    # Check plugin background checks
    try:
        checks = await plugin_loader.get_plugin_background_checks()
        for plugin_name, check_fn in checks:
            try:
                result = await check_fn() if asyncio.iscoroutinefunction(check_fn) else check_fn()
                if not result:
                    continue
                # Plugins return either:
                #   - a single dict with "prompt" key (gym, habit, etc.)
                #   - a list of dicts with "task_id" key (todo reminders)
                #   - a list of dicts with "prompt" key (money spending nags)
                if isinstance(result, dict):
                    result = [result]
                for item in result:
                    if isinstance(item, dict) and "prompt" in item and "task_id" not in item:
                        # Prompt-based plugin check — once per day per type
                        from datetime import timezone, timedelta
                        today = __import__("datetime").datetime.now(
                            timezone(timedelta(hours=7))
                        ).strftime("%Y-%m-%d")
                        key = f"{plugin_name}-{item.get('type', 'check')}"
                        if _check_fired_today.get(key) == today:
                            continue
                        _check_fired_today[key] = today
                        _save_daily_state()
                        prompt = item["prompt"]
                        ctx = item.get("context", f"{plugin_name}_check")
                        await enqueue(QueueItem(
                            type="reminder",
                            handler=lambda p=prompt, c=ctx: _speak(p, c),
                            label=f"{plugin_name}: {item.get('type', 'check')}",
                        ))
                        reset_idle_timer()
                    else:
                        reminders.append(item)
            except Exception as e:
                logger.debug(f"[background] Plugin check failed for {plugin_name}: {e}")
    except Exception:
        pass

    # Fallback: try HTTP call to task-manager microapp if plugin didn't provide data
    if not reminders and hasattr(config, "TASK_MANAGER_URL") and config.TASK_MANAGER_URL:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{config.TASK_MANAGER_URL}/tasks/reminders/upcoming",
                    params={"within_minutes": 15},
                )
                if resp.status_code == 200:
                    reminders = resp.json()
        except Exception:
            pass

    for r in reminders:
        key = f"{r['task_id']}-{r.get('due_time', '')}"
        if key in _seen_reminders:
            continue
        _seen_reminders.add(key)

        title = r["title"]
        minutes = r["minutes_until_due"]
        prompt = (
            f"Remind the user: \"{title}\" is due in {minutes} minutes. "
            f"Say it naturally and in-character, keep it short (1 sentence)."
        )

        await ntfy.notify(
            title=f"Task Reminder: {title}",
            message=f"Due in {minutes} minutes",
            priority=4,
            tags=["alarm_clock"],
        )

        await enqueue(QueueItem(
            type="reminder",
            handler=lambda p=prompt: _speak(p, "reminder"),
            label=f"{title} ({minutes}m)",
        ))

        reset_idle_timer()

    if len(_seen_reminders) > 200:
        _seen_reminders.clear()


async def _check_spending():
    """Check spending status and nag the user if thresholds are crossed."""
    global _last_spending_nag

    # Try plugin first
    money_plugin = plugin_loader.get_plugin("money")
    if money_plugin and money_plugin.get("handler"):
        try:
            handler = money_plugin["handler"]
            if hasattr(handler, "check_spending"):
                nags = await handler.check_spending()
                for nag in (nags or []):
                    notify_data = nag.get("notify", {})
                    if notify_data:
                        await ntfy.notify(**notify_data)
                    prompt = nag.get("prompt")
                    ctx = nag.get("context", "spending_nag")
                    if prompt:
                        logger.info(f"[background] Spending nag (plugin): {ctx}")
                        await enqueue(QueueItem(
                            type="idle_talk",
                            handler=lambda p=prompt, c=ctx: _speak(p, c),
                            label=ctx,
                        ))
                return
        except Exception as e:
            logger.debug(f"[background] Money plugin check_spending failed: {e}")

    # Fallback: HTTP to money-manager microapp
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{config.MONEY_MANAGER_URL}/spending-status")
            if resp.status_code != 200:
                return
            status = resp.json()
    except Exception:
        return

    threshold = status.get("threshold", "ok")
    percent = status.get("spent_percent", 0)

    if threshold == "ok" or threshold == _last_spending_nag:
        return

    prompts = _SPENDING_PROMPTS.get(threshold, [])
    if not prompts:
        return

    _last_spending_nag = threshold
    prompt = random.choice(prompts).format(percent=percent)
    logger.info(f"[background] Spending nag triggered: {threshold} ({percent}%)")

    await ntfy.notify(
        title=f"Spending {threshold.upper()}",
        message=f"You've spent {percent}% of your monthly budget!",
        priority=4,
        tags=["money_with_wings"],
    )

    await enqueue(QueueItem(
        type="idle_talk",
        handler=lambda p=prompt: _speak(p, "spending_nag"),
        label=f"spending_{threshold}",
    ))


async def _fetch_idle_tool_prompt() -> str | None:
    """Fetch real data from task/money manager and build a contextual idle prompt.

    Returns None if no services are reachable or no interesting data found.
    """
    prompts: list[str] = []

    # Plugin idle prompts (todo, money, calorie, sensor, etc.)
    try:
        plugin_prompts = plugin_loader.get_plugin_idle_prompts()
        prompts.extend(plugin_prompts)
    except Exception:
        pass

    # Fallback: HTTP to task-manager microapp (if plugin not yet migrated)
    if not any("task" in p.lower() or "to-do" in p.lower() for p in prompts):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{config.TASK_MANAGER_URL}/tasks",
                    params={"completed": "false"},
                )
                if resp.status_code == 200:
                    tasks = resp.json()
                    if not tasks:
                        prompts.append(_IDLE_TOOL_NO_TASKS)
                    else:
                        from datetime import datetime, timezone
                        now = datetime.now(timezone.utc)
                        overdue = []
                        upcoming = []
                        for t in tasks:
                            is_prayer = t.get("source") == "prayer" or t.get("category") == "prayer"
                            if t.get("due_time"):
                                due = datetime.fromisoformat(t["due_time"])
                                if due < now:
                                    if not is_prayer:
                                        overdue.append(t["title"])
                                else:
                                    upcoming.append(t["title"])
                            else:
                                upcoming.append(t["title"])

                        if overdue:
                            prompts.append(_IDLE_TOOL_OVERDUE.format(
                                count=len(overdue),
                                tasks=", ".join(overdue[:3]),
                            ))
                        elif upcoming:
                            prompts.append(_IDLE_TOOL_UPCOMING.format(
                                tasks=", ".join(upcoming[:3]),
                            ))
        except Exception:
            pass

    # Money idle: plugin handles it via get_plugin_idle_prompts() above
    # Fallback to HTTP only if money plugin not loaded
    if not plugin_loader.get_plugin("money"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{config.MONEY_MANAGER_URL}/spending-status")
                if resp.status_code == 200:
                    status = resp.json()
                    percent = status.get("spent_percent", 0)
                    amount = status.get("monthly_expense", 0)
                    if amount > 0:
                        prompts.append(_IDLE_TOOL_SPENDING.format(
                            amount=f"Rp {amount:,}".replace(",", "."),
                            percent=int(percent),
                        ))
        except Exception:
            pass

    # Calorie idle: plugin handles it via get_plugin_idle_prompts() above
    # Fallback to HTTP only if calorie plugin not loaded
    if not plugin_loader.get_plugin("calorie"):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{config.CALORIE_TRACKER_URL}/calorie-status")
                if resp.status_code == 200:
                    cstatus = resp.json()
                    total_eaten = cstatus.get("total_eaten", 0)
                    target = cstatus.get("daily_target", 2000)
                    percent = cstatus.get("eaten_percent", 0)
                    if total_eaten > 0:
                        prompts.append(_IDLE_CALORIES.format(
                            calories=total_eaten,
                            percent=int(percent),
                            target=target,
                        ))
                    else:
                        prompts.append(_IDLE_CALORIES_NONE)
        except Exception:
            pass

    if config.NEXTCLOUD_ENABLED:
        try:
            events = await calendar_service.fetch_upcoming_events(hours_ahead=24)
            if events:
                from datetime import timezone, timedelta
                wib = timezone(timedelta(hours=7))
                evt = events[0]
                time_str = evt["start"].astimezone(wib).strftime("%H:%M WIB")
                prompts.append(_IDLE_CALENDAR.format(title=evt["title"], time=time_str))
        except Exception:
            pass

    try:
        times = await prayer.fetch_prayer_times()
        if times:
            name, time_str, minutes = prayer.get_next_prayer(times["timings"])
            if name and minutes is not None and 0 < minutes <= 15:
                prompts.append(_IDLE_PRAYER.format(name=name, time=time_str, minutes=minutes))
    except Exception:
        pass

    if config.FRESHRSS_ENABLED:
        try:
            headline = await freshrss.get_random_headline()
            if headline:
                if headline.get("is_character_post"):
                    prompts.append(_idle_rss_character_prompt(headline["title"], headline["source"]))
                else:
                    prompts.append(_IDLE_RSS_GENERAL.format(title=headline["title"], source=headline["source"]))
        except Exception:
            pass

    try:
        from services.geolocation import get_location
        geo = await get_location()
        data = await weather.fetch_weather(geo["lat"], geo["lon"])
        if data:
            c = data["current"]
            summary = f"{c['description']}, {c['temperature']}°C (feels like {c['feels_like']}°C)"
            prompts.append(_IDLE_WEATHER.format(weather=summary))
    except Exception:
        pass

    if not prompts:
        return None
    if len(prompts) == 1:
        return prompts[0]
    weights = [3, 2] + [1] * (len(prompts) - 2)
    return random.choices(prompts, weights=weights[:len(prompts)], k=1)[0]


async def _check_calendar_reminders():
    """Check for upcoming calendar events and enqueue reminders."""
    if not config.NEXTCLOUD_ENABLED:
        return

    try:
        events = await calendar_service.get_events_within(minutes=30)
    except Exception:
        return

    from datetime import timezone, timedelta
    now_utc = __import__("datetime").datetime.now(timezone.utc)
    wib = timezone(timedelta(hours=7))

    for evt in events:
        key = f"cal-{evt['title']}-{evt['start'].isoformat()}"
        if key in _seen_reminders:
            continue
        _seen_reminders.add(key)

        minutes_left = int((evt["start"] - now_utc).total_seconds() / 60)
        title = evt["title"]
        prompt = (
            f"Remind the user: '{title}' starts in {minutes_left} minutes. "
            f"Say it naturally. One sentence."
        )

        await ntfy.notify(
            title=f"Calendar: {title}",
            message=f"Starts in {minutes_left} minutes",
            priority=4,
            tags=["calendar"],
        )

        await enqueue(QueueItem(
            type="reminder",
            handler=lambda p=prompt: _speak(p, "calendar_reminder"),
            label=f"cal: {title} ({minutes_left}m)",
        ))

        reset_idle_timer()


    # Prayer reminders moved to plugins/prayer/ (background_task with run_during_sleep)


async def _check_calendar_reminders_silent():
    """Silent calendar reminders during sleep — Telegram push only, no LLM/TTS."""
    if not config.NEXTCLOUD_ENABLED:
        return
    try:
        events = await calendar_service.get_events_within(minutes=30)
    except Exception:
        return

    from datetime import timezone, timedelta
    now_utc = __import__("datetime").datetime.now(timezone.utc)

    for evt in events:
        key = f"cal-{evt['title']}-{evt['start'].isoformat()}"
        if key in _seen_reminders:
            continue
        _seen_reminders.add(key)

        minutes_left = int((evt["start"] - now_utc).total_seconds() / 60)
        await ntfy.notify(
            title=f"Calendar: {evt['title']}",
            message=f"Starts in {minutes_left} minutes",
            priority=4,
        )


    # Character post detection moved to plugins/rss/ (background_task)

        reset_idle_timer()


async def _check_session_summary():
    """Auto-summarize conversation when idle for 30+ min and there are unsummarized messages."""
    global _last_summarized_at

    if _last_user_interaction == 0.0:
        return

    idle_minutes = (time.time() - _last_user_interaction) / 60
    if idle_minutes < 30:
        return

    # Don't re-summarize within 30 min
    if time.time() - _last_summarized_at < 1800:
        return

    unsummarized = memory.get_unsummarized_messages()
    if len(unsummarized) < 4:  # need at least 2 exchanges
        return

    _last_summarized_at = time.time()

    # Build a transcript for the LLM to summarize
    transcript_lines = []
    for msg in unsummarized:
        role = "User" if msg["role"] == "user" else config.CHARACTER_NAME
        transcript_lines.append(f"{role}: {msg['content']}")
    transcript = "\n".join(transcript_lines[-40:])  # cap at last 40 messages

    summary_prompt = (
        "Summarize this conversation in 2-3 sentences. "
        "Focus on key topics discussed, decisions made, and emotional moments. "
        "Write from a third-person perspective.\n\n"
        f"{transcript}"
    )

    try:
        from openai import OpenAI
        import asyncio

        # Use fallback chain for summarization when primary is Claude (no OpenAI endpoint)
        base_url = config.get_llm_base_url()
        api_key = config.get_llm_api_key()
        model = config.get_llm_model()

        if not base_url or config.LLM_PROVIDER == "claude":
            fallbacks = config.get_llm_fallback_chain()
            if not fallbacks:
                return  # No fallback available
            fb = fallbacks[0]
            base_url = fb["base_url"]
            api_key = fb["api_key"]
            model = fb["model"]

        if not base_url:
            return

        client = OpenAI(
            base_url=base_url,
            api_key=api_key or "no-key",
            timeout=15,
        )

        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=model,
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.3,
            max_tokens=150,
        )

        summary = response.choices[0].message.content or ""
        summary = summary.strip()
        if not summary:
            return

        # Strip any emotion tags the model might add
        import re
        summary = re.sub(r"^\s*\[[A-Z]+\]\s*", "", summary).strip()

        start_time = unsummarized[0].get("timestamp", "")
        end_time = unsummarized[-1].get("timestamp", "")

        memory.add_session_summary(
            summary=summary,
            message_count=len(unsummarized),
            start_time=start_time,
            end_time=end_time,
        )
        logger.info(f"[background] Session summary generated ({len(unsummarized)} msgs): {summary[:80]}...")
    except Exception as e:
        logger.error(f"[background] Session summary failed: {e}")


async def _smart_idle_speak(topic: str):
    """Run smart idle: Sonnet + WebSearch for an interesting topic, then speak."""
    from services.claude_cli import query_claude_cli

    system_prompt = llm.get_system_prompt()
    prompt = (
        f"Search the web for something interesting about '{topic}' that happened recently. "
        f"Share it naturally as {config.CHARACTER_NAME} would. Keep it to 1-2 sentences. "
        f"Prefix your reply with an emotion tag like [HAPPY] or [SURPRISED]."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"[Inner thought: {prompt}]"},
    ]

    raw = await query_claude_cli(
        messages=messages,
        model=config.CLAUDE_CLI_SMART_MODEL,
        effort=config.CLAUDE_CLI_SMART_EFFORT,
        timeout=config.CLAUDE_CLI_SMART_TIMEOUT,
        allowed_tools=config.CLAUDE_CLI_SMART_TOOLS,
    )
    if not raw:
        logger.warning("[background] Smart idle: Claude CLI returned nothing")
        return

    emotion, clean_reply = llm.parse_emotion(raw)
    clean_reply = llm._clean_reply_text(clean_reply)

    if not clean_reply:
        return

    memory.add_message("assistant", clean_reply, emotion=emotion)

    audio_url = None
    if config.TTS_ENABLED and tts_service.is_ready() and manager.client_count > 0:
        audio_url = await asyncio.to_thread(
            tts_service.synthesize, clean_reply, None, 1.0, emotion.lower()
        )

    if manager.client_count > 0:
        await manager.broadcast({
            "type": "chat",
            "reply": clean_reply,
            "emotion": emotion,
            "audio_url": audio_url,
            "context": "smart_idle",
            "user_name": "System",
        })

    await ntfy.notify(title=config.CHARACTER_NAME, message=clean_reply)

    if config.STICKER_ENABLED and random.random() < config.STICKER_CHANCE:
        sticker_id = sticker.resolve(clean_reply, emotion)
        if sticker_id:
            await ntfy.send_sticker(sticker_id)

    chat_logger.info(f"[smart_idle] {config.CHARACTER_NAME} [{emotion}]: {clean_reply}")
    logger.info(f"[background] Smart idle ({topic}): [{emotion}] {clean_reply[:80]}...")


def _fetch_sensor_prompt() -> str | None:
    """Build a sensor-based idle prompt. Returns None if no sensor data available."""
    try:
        latest = sensor.get_today_summary()
        if not latest:
            return None
    except Exception:
        return None

    prompts: list[str] = []
    triggered = False  # True if any threshold was crossed

    st = latest.get("screen_time", {})
    if st:
        top_app = max(st, key=st.get)
        mins = st[top_app]
        if mins >= 180:
            prompts.append(_SENSOR_SCREEN_ANGRY.format(app=top_app, minutes=mins))
            triggered = True
        elif mins >= 120:
            prompts.append(_SENSOR_SCREEN_ANNOYED.format(app=top_app, minutes=mins))
            triggered = True
        elif mins >= 60:
            prompts.append(_SENSOR_SCREEN_GENTLE.format(app=top_app, minutes=mins))
            triggered = True
        elif mins > 0:
            prompts.append(f'The user has spent {mins} minutes on {top_app} today. Comment casually. One sentence.')

    steps = latest.get("steps", 0)
    if steps > 0:
        from datetime import timezone, timedelta as td
        hour = __import__("datetime").datetime.now(timezone(td(hours=7))).hour
        if steps >= 10000:
            prompts.append(_SENSOR_STEPS_PRAISE.format(steps=f"{steps:,}"))
            triggered = True
        elif steps >= 7000:
            prompts.append(_SENSOR_STEPS_ACTIVE.format(steps=f"{steps:,}"))
            triggered = True
        elif steps < 3000 and hour >= 18:
            prompts.append(_SENSOR_STEPS_LAZY.format(steps=f"{steps:,}"))
            triggered = True
        else:
            prompts.append(f'The user has walked {steps:,} steps today. Comment casually. One sentence.')

    if not prompts:
        return None
    return random.choice(prompts), triggered


async def _check_step_goal():
    """At 18:00+ WIB, check if user hit their step goal. Once per day."""
    global _step_goal_nagged_date

    from datetime import timezone, timedelta
    wib = timezone(timedelta(hours=7))
    now = __import__("datetime").datetime.now(wib)

    if now.hour < 18:
        return

    today = now.strftime("%Y-%m-%d")
    if _step_goal_nagged_date == today:
        return

    data = sensor.get_today_summary()
    if not data or "steps" not in data:
        return

    steps = data["steps"]
    goal = sensor.get_step_goal()

    _step_goal_nagged_date = today
    _save_daily_state()

    if steps >= goal:
        prompt = (
            f"The user has walked {steps:,} steps today, reaching their daily goal of {goal:,}! "
            f"Be genuinely proud and praise them. One sentence."
        )
        context = "step_goal_reached"
    else:
        remaining = goal - steps
        prompt = (
            f"It's evening and the user has only walked {steps:,} steps today — "
            f"their goal is {goal:,} and they're {remaining:,} steps short! "
            f"Be angry and scold them for being lazy. One sentence."
        )
        context = "step_goal_missed"

    logger.info(f"[background] Step goal check: {steps}/{goal} — {context}")
    await enqueue(QueueItem(
        type="sensor_nag",
        handler=lambda p=prompt: _speak(p, context),
        label=f"step_goal: {steps}/{goal}",
    ))


async def _check_idle():
    """Check if enough time has passed without interaction, enqueue idle talk.

    Distribution:
      - Smart idle: 20% (Sonnet + WebSearch, claude provider only)
      - Tool-based: 30% (tasks, spending, calories, calendar, weather, RSS)
      - Random:     30% (creative unprompted talk)
      - Sensor:     20% base → 30% if threshold triggered today
    """
    if _last_user_interaction == 0.0:
        return

    elapsed = time.time() - _last_user_interaction
    idle_threshold = config.IDLE_TALK_INTERVAL_HOURS * 3600

    if elapsed < idle_threshold:
        return

    reset_idle_timer()

    # Determine sensor weight: 20% base, 50% if threshold triggered
    sensor_result = _fetch_sensor_prompt()
    sensor_prompt = None
    sensor_triggered = False
    if sensor_result:
        sensor_prompt, sensor_triggered = sensor_result
    sensor_chance = 0.30 if sensor_triggered else 0.20

    # Build weighted choices, filter out categories on cooldown
    smart_enabled = (config.LLM_PROVIDER == "claude"
                     and config.CLAUDE_CLI_SMART_IDLE_ENABLED)

    candidates = []  # (category, weight)

    if smart_enabled and not _idle_on_cooldown("smart"):
        candidates.append(("smart", 0.20))
    if not _idle_on_cooldown("tool"):
        candidates.append(("tool", 0.30))
    if not _idle_on_cooldown("random"):
        candidates.append(("random", 0.30))
    if sensor_prompt and not _idle_on_cooldown("sensor"):
        candidates.append(("sensor", sensor_chance))

    # If all on cooldown, reset and allow everything
    if not candidates:
        _idle_recent.clear()
        if smart_enabled:
            candidates.append(("smart", 0.20))
        candidates.append(("tool", 0.30))
        candidates.append(("random", 0.30))
        if sensor_prompt:
            candidates.append(("sensor", sensor_chance))

    choices = [c[0] for c in candidates]
    weights = [c[1] for c in candidates]
    pick = random.choices(choices, weights=weights, k=1)[0]
    _idle_record(pick)

    if pick == "smart":
        topics = config.CLAUDE_CLI_SMART_IDLE_TOPICS
        topic = random.choice(topics.split(",")).strip()
        logger.info(f"[background] Smart idle triggered: topic='{topic}' ({elapsed / 3600:.1f}h)")
        await enqueue(QueueItem(
            type="idle_talk",
            handler=lambda t=topic: _smart_idle_speak(t),
            label=f"smart_idle: {topic}",
        ))
    elif pick == "sensor":
        logger.info(f"[background] Sensor idle triggered (threshold={sensor_triggered}, {elapsed / 3600:.1f}h)")
        await enqueue(QueueItem(
            type="idle_talk",
            handler=lambda p=sensor_prompt: _speak(p, "sensor_idle"),
            label="sensor_idle",
        ))
    elif pick == "tool":
        prompt = await _fetch_idle_tool_prompt()
        if prompt:
            logger.info(f"[background] Idle talk (tool-based) triggered ({elapsed / 3600:.1f}h)")
            await enqueue(QueueItem(
                type="idle_talk",
                handler=lambda p=prompt: _speak(p, "idle_talk_tool"),
                label="idle_tool",
            ))
        else:
            # Fallback to random if no tool data
            _idle_record("random")  # count fallback as random
            prompt = _build_idle_random_prompt()
            logger.info(f"[background] Idle talk (random, tool fallback) triggered ({elapsed / 3600:.1f}h)")
            await enqueue(QueueItem(
                type="idle_talk",
                handler=lambda p=prompt: _speak(p, "idle_talk"),
                label="idle",
            ))
    else:  # random
        prompt = _build_idle_random_prompt()
        logger.info(f"[background] Idle talk (random) triggered ({elapsed / 3600:.1f}h)")
        await enqueue(QueueItem(
            type="idle_talk",
            handler=lambda p=prompt: _speak(p, "idle_talk"),
            label="idle",
        ))


async def _check_scheduled_actions():
    """Check plugin scheduled_actions and fire them at the configured time (once per day)."""
    from datetime import timezone, timedelta

    plugins = plugin_loader.get_enabled_plugins()
    for name, entry in plugins.items():
        manifest = entry["manifest"]
        actions = manifest.get("scheduled_actions", [])
        handler_mod = entry.get("handler")

        for action in actions:
            if not action.get("enabled"):
                continue

            fn_name = action.get("function")
            target_time = action.get("time")  # "HH:MM"
            tz_name = action.get("timezone", "Asia/Jakarta")

            if not fn_name or not target_time:
                continue

            # Parse timezone
            tz_offset = 7 if "Jakarta" in tz_name else 0  # Simple WIB default
            tz = timezone(timedelta(hours=tz_offset))
            now = __import__("datetime").datetime.now(tz)
            today = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")

            # Dedup: only fire once per day per action
            key = f"{name}:{action['name']}"
            if _scheduled_fired_today.get(key) == today:
                continue

            # Check if current time matches (within 1 minute window)
            if current_time != target_time:
                continue

            _scheduled_fired_today[key] = today
            _save_daily_state()

            # Fire the action
            fn = getattr(handler_mod, fn_name, None) if handler_mod else None
            if fn:
                try:
                    result = await fn() if asyncio.iscoroutinefunction(fn) else fn()
                    if isinstance(result, str) and result:
                        await enqueue(QueueItem(
                            type="scheduled",
                            handler=lambda p=result: _speak(p, f"scheduled_{name}"),
                            label=f"scheduled: {name}/{action['name']}",
                        ))
                    logger.info(f"[background] Scheduled action fired: {name}/{action['name']}")
                except Exception as e:
                    logger.error(f"[background] Scheduled action {name}/{action['name']} failed: {e}")

    # Clean old dates from tracking
    today = __import__("datetime").datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
    for key in list(_scheduled_fired_today):
        if _scheduled_fired_today[key] != today:
            del _scheduled_fired_today[key]


async def _run_plugin_background_tasks(is_sleeping: bool):
    """Run plugin background tasks. Tasks with run_during_sleep=True run always."""
    tasks = plugin_loader.get_plugin_background_tasks()
    for name, fn, run_during_sleep in tasks:
        if is_sleeping and not run_during_sleep:
            continue
        try:
            await fn() if asyncio.iscoroutinefunction(fn) else fn()
        except Exception as e:
            logger.warning(f"[background] Plugin task {name} failed: {e}")


async def _main_loop():
    """Single background loop — checks both reminders and idle every 60s."""
    _load_daily_state()
    interval = config.TASK_REMINDER_POLL_SECONDS
    bg_tasks = plugin_loader.get_plugin_background_tasks()
    bg_checks = await plugin_loader.get_plugin_background_checks()
    logger.info(f"[background] Started (every {interval}s, idle after {config.IDLE_TALK_INTERVAL_HOURS}h, "
                f"plugin_tasks={len(bg_tasks)}, plugin_checks={len(bg_checks)})")

    while True:
        try:
            await asyncio.sleep(interval)

            # Keep WebSocket alive even during sleep
            if manager.client_count > 0:
                await manager.ping_all()

            global _last_sleep_state
            current_sleeping = sleep.is_sleeping()
            if _last_sleep_state is not None and current_sleeping != _last_sleep_state:
                if manager.client_count > 0:
                    await manager.broadcast({"type": "sleep", "sleeping": current_sleeping})
                    logger.info(f"[background] Sleep state broadcast: sleeping={current_sleeping}")
            _last_sleep_state = current_sleeping

            # Run plugin background tasks (some may run during sleep)
            await _run_plugin_background_tasks(current_sleeping)

            if current_sleeping:
                await _check_calendar_reminders_silent()
                continue

            await _check_session_summary()
            await _check_reminders()
            await _check_calendar_reminders()
            # Prayer + RSS moved to plugins (background_task)
            await _check_spending()
            await _check_step_goal()
            await _check_scheduled_actions()
            await _check_idle()

            # Reset stale roulette state
            from services.tools.roulette_tool import check_roulette_timeout
            check_roulette_timeout()

            if config.MOOD_ENABLED:
                mood.decay_toward_equilibrium()
        except asyncio.CancelledError:
            logger.info("[background] Stopped")
            break
        except Exception as e:
            import traceback as _tb
            logger.error(f"[background] Loop error: {e}\n{_tb.format_exc()}")


async def check_money_manager() -> str:
    """Health check — is the Money Manager reachable?"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.MONEY_MANAGER_URL}/health")
            if resp.status_code == 200:
                return "ok"
    except Exception:
        pass
    return "offline"


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


async def check_calorie_tracker() -> str:
    """Health check — is the Calorie Tracker reachable?"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{config.CALORIE_TRACKER_URL}/health")
            if resp.status_code == 200:
                return "ok"
    except Exception:
        pass
    return "offline"


def start():
    global _task, _last_user_interaction
    if _task is not None:
        return
    _last_user_interaction = time.time()
    _task = asyncio.create_task(_main_loop())


def stop():
    global _task
    if _task:
        _task.cancel()
        _task = None
