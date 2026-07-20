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
from collections import deque
import httpx
from services import llm, tts_service, memory, weather, ntfy, freshrss, calendar_service, prayer, sleep, mood, sticker, plugin_loader
from services.ws_manager import manager
from services.message_queue import QueueItem, enqueue
from utils import time as ltime
import config

logger = logging.getLogger(__name__)
chat_logger = logging.getLogger("chat")

_task: asyncio.Task | None = None
_seen_reminders: set[str] = set()
_last_user_interaction: float = 0.0
_last_sleep_state: bool | None = None
_last_summarized_at: float = 0.0

# Smart-idle dedup: track last 24h of replies so the WebSearch'd topic
# doesn't repeat across the long gap between firings. format_recent_assistant
# only carries the last 10 messages of any kind, which gets evicted by normal
# chat between two smart-idle calls 12+ hours apart.
_smart_idle_history: deque[dict] = deque(maxlen=30)
_SMART_IDLE_DEDUP_HOURS = 24.0

def _build_idle_random_prompt() -> str:
    """Build a dynamic idle talk prompt with real-time context for the LLM to decide what to say."""
    now_wib = ltime.now()
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
        f"It's {ltime.fmt_time(now_wib, '%H:%M')} ({time_of_day}). "
        f"Your user has been quiet for about {idle_minutes} minutes. "
        "Say something unprompted and in-character. You decide what — "
        "it could be anything: a random thought, teasing the user, "
        "commenting on the time, humming to yourself, complaining about boredom, "
        "bragging about something, sharing an opinion, a dramatic monologue about being ignored, "
        "or whatever feels natural right now. Be creative, don't repeat yourself. "
        "One sentence max."
    )


def _build_memory_idle_prompt() -> str | None:
    """Pick a recent core memory or session summary and craft a memory-driven idle prompt.

    60% core memory (deeper facts), 40% recent session summary (fresh conversation).
    Returns None if no memory is available — caller should fall back to random.
    """
    # 60/40 roll for source
    if random.random() < 0.60:
        mems = memory.get_recent_core_memories(limit=15)
        if mems:
            pick = random.choice(mems)
            return (
                f"You remembered something about your user: '{pick['content']}'. "
                f"Bring it up naturally as if it just crossed your mind — ask about it, "
                f"react to it, tease them, or check in on it. ONE sentence. "
                f"Prefix with an emotion tag like [HAPPY], [ANGRY], or [SURPRISED]."
            )
        # Fall through to summary if no core memories
    summaries = memory.get_recent_summaries(days=2, limit=5)
    if summaries:
        pick = random.choice(summaries)
        return (
            f"Earlier you had this conversation with your user: '{pick['summary']}'. "
            f"Circle back to it now — follow up, check in, or tease them about something from it. "
            f"ONE sentence. Prefix with an emotion tag."
        )
    # Final fallback: any core memory if we haven't tried it yet
    mems = memory.get_recent_core_memories(limit=15)
    if mems:
        pick = random.choice(mems)
        return (
            f"You remembered something about your user: '{pick['content']}'. "
            f"Bring it up naturally. ONE sentence. Prefix with an emotion tag."
        )
    return None

_IDLE_WEATHER = "Current weather: {weather}. Make a casual comment about the weather — react naturally as if you looked outside. One sentence."
_IDLE_RSS_GENERAL = 'You just saw this news headline: "{title}" from {source}. Comment on it briefly — react naturally. One sentence.'
def _idle_rss_character_prompt(title: str, source: str) -> str:
    return f'You ({config.CHARACTER_NAME}) just posted something! Title: "{title}" on {source}. Excitedly tell your user about it — like "Hey, I just uploaded a new video!" or "I just tweeted something, check it out~". One sentence.'
_IDLE_CALENDAR = 'The user has an upcoming event: "{title}" at {time}. Mention it casually. One sentence.'
_IDLE_PRAYER = 'Next prayer is {name} at {time} ({minutes} minutes from now). Gently remind the user. One sentence.'

_scheduled_fired_today: dict[str, str] = {}  # "plugin:action_name" -> date fired
_check_fired_today: dict[str, str] = {}  # "plugin-type" -> date fired

# Idle cooldown: track recent idle categories to avoid repeats
# Each entry is the category name, last 4 kept. A category in this list gets skipped.
_IDLE_COOLDOWN_SIZE = 5
_idle_recent: list[str] = []  # e.g. ["memory", "tool", "random", "smart", "tool"]

def _idle_on_cooldown(category: str) -> bool:
    """Check if this idle category was used in the last N cycles."""
    return category in _idle_recent  # max 1 out of last 5

def _idle_record(category: str):
    """Record that this idle category just fired."""
    _idle_recent.append(category)
    while len(_idle_recent) > _IDLE_COOLDOWN_SIZE:
        _idle_recent.pop(0)

# Smart idle topic dedup: track topics used in last N hours to avoid repetition
_smart_topic_history: list[tuple[str, float]] = []  # (topic, timestamp)
_SMART_TOPIC_COOLDOWN_HOURS = 12

def _pick_fresh_topic() -> str | None:
    """Pick a smart idle topic not used in the last N hours."""
    cutoff = time.time() - (_SMART_TOPIC_COOLDOWN_HOURS * 3600)
    _smart_topic_history[:] = [(t, ts) for t, ts in _smart_topic_history if ts > cutoff]
    used = {t for t, _ in _smart_topic_history}
    all_topics = [t.strip() for t in config.CLAUDE_CLI_SMART_IDLE_TOPICS.split(",") if t.strip()]
    available = [t for t in all_topics if t not in used]
    if not available:
        _smart_topic_history.clear()
        available = all_topics
    if not available:
        return None
    topic = random.choice(available)
    _smart_topic_history.append((topic, time.time()))
    return topic

# Persist daily dedup state across restarts
_DAILY_STATE_PATH = "data/background_daily.json"

def _load_daily_state():
    """Load persisted daily dedup state from disk. Resets if date changed."""
    global _scheduled_fired_today, _check_fired_today
    import json
    from pathlib import Path
    today = ltime.now().strftime("%Y-%m-%d")
    try:
        state = json.loads(Path(_DAILY_STATE_PATH).read_text())
        # Reset if saved date != today
        if state.get("date") != today:
            logger.info(f"[background] Daily state expired (was {state.get('date')}, now {today}), resetting")
            _save_daily_state()
            return
        _scheduled_fired_today = state.get("scheduled_fired", {})
        _check_fired_today = state.get("check_fired", {})
        logger.info(f"[background] Loaded daily state: checks={list(_check_fired_today.keys())}")
    except Exception:
        pass

def _save_daily_state():
    """Persist daily dedup state to disk."""
    import json
    from pathlib import Path
    today = ltime.now().strftime("%Y-%m-%d")
    try:
        Path(_DAILY_STATE_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(_DAILY_STATE_PATH).write_text(json.dumps({
            "date": today,
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

    # Time awareness: prepend current local time so she can reference it naturally
    now_wib = ltime.now()
    time_prefix = f"[Current time: {ltime.fmt_time(now_wib, '%A %H:%M')}]\n"

    # Anti-repetition: inject last 10 assistant utterances so she doesn't repeat herself
    recent_block = memory.format_recent_assistant_for_prompt(limit=10)

    full_prompt = time_prefix + prompt + (("\n" + recent_block) if recent_block else "")

    result = await llm.chat(message=full_prompt, context=context, user_name="System", is_system_prompt=True)

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
                        today = ltime.now().strftime("%Y-%m-%d")
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


async def _fetch_idle_tool_prompt() -> str | None:
    """Fetch real data from task/money manager and build a contextual idle prompt.

    Returns None if no services are reachable or no interesting data found.
    """
    prompts: list[str] = []

    # Plugin idle prompts (todo, money, calorie, etc.)
    try:
        plugin_prompts = plugin_loader.get_plugin_idle_prompts()
        prompts.extend(plugin_prompts)
    except Exception:
        pass

    # Task, money, and calorie idle prompts all come from their plugins via
    # get_plugin_idle_prompts() above — no engine-side per-plugin fallback.

    if config.NEXTCLOUD_ENABLED:
        try:
            events = await calendar_service.fetch_upcoming_events(hours_ahead=24)
            if events:
                evt = events[0]
                time_str = ltime.fmt_time(evt["start"].astimezone(ltime.get_tz()), "%H:%M")
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

    from datetime import timezone
    now_utc = __import__("datetime").datetime.now(timezone.utc)

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

    from datetime import timezone
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

    # Time + anti-repetition injection (mirrors _speak)
    now_wib = ltime.now()
    time_prefix = f"[Current time: {ltime.fmt_time(now_wib, '%A %H:%M')}]\n"
    recent_block = memory.format_recent_assistant_for_prompt(limit=10)

    # Smart-idle-specific dedup over 24h — stops "HoloEN concert"
    # being announced again 13h after the first time.
    cutoff = time.time() - (_SMART_IDLE_DEDUP_HOURS * 3600)
    recent_smart = [h for h in _smart_idle_history if h["ts"] >= cutoff]
    if recent_smart:
        avoid_lines = ["\n## ALREADY COVERED in last 24h — pick a DIFFERENT story/angle, do NOT recycle these:"]
        for h in recent_smart:
            avoid_lines.append(f"- ({h['topic']}) {h['snippet']}")
        avoid_block = "\n".join(avoid_lines) + "\n"
    else:
        avoid_block = ""

    prompt = (
        f"Search the web for something interesting about '{topic}' that happened recently. "
        f"Share it naturally as {config.CHARACTER_NAME} would. Keep it to 1-2 sentences. "
        f"Prefix your reply with an emotion tag like [HAPPY] or [SURPRISED]."
    )
    full_prompt = time_prefix + prompt + avoid_block + (("\n" + recent_block) if recent_block else "")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"[Inner thought: {full_prompt}]"},
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

    _smart_idle_history.append({"ts": time.time(), "topic": topic, "snippet": clean_reply[:180]})

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


async def _check_idle():
    """Check if enough time has passed without interaction, enqueue idle talk.

    Distribution:
      - Smart idle: 20% (Sonnet + WebSearch, claude provider only)
      - Tool-based: 30% (tasks, spending, calories, calendar, weather, RSS)
      - Memory:     25% (pulls from core memories + recent session summaries)
      - Random:     5%  (creative unprompted talk)
    """
    if _last_user_interaction == 0.0:
        return

    elapsed = time.time() - _last_user_interaction
    idle_threshold = config.IDLE_TALK_INTERVAL_HOURS * 3600

    if elapsed < idle_threshold:
        return

    reset_idle_timer()

    # Build weighted choices, filter out categories on cooldown
    smart_enabled = (config.LLM_PROVIDER == "claude"
                     and config.CLAUDE_CLI_SMART_IDLE_ENABLED)

    candidates = []  # (category, weight)

    if smart_enabled and not _idle_on_cooldown("smart"):
        candidates.append(("smart", 0.20))
    if not _idle_on_cooldown("tool"):
        candidates.append(("tool", 0.30))
    if not _idle_on_cooldown("memory"):
        candidates.append(("memory", 0.25))
    if not _idle_on_cooldown("random"):
        candidates.append(("random", 0.05))

    # If all on cooldown, reset and allow everything
    if not candidates:
        _idle_recent.clear()
        if smart_enabled:
            candidates.append(("smart", 0.20))
        candidates.append(("tool", 0.30))
        candidates.append(("memory", 0.25))
        candidates.append(("random", 0.05))

    choices = [c[0] for c in candidates]
    weights = [c[1] for c in candidates]
    pick = random.choices(choices, weights=weights, k=1)[0]
    _idle_record(pick)

    if pick == "smart":
        topic = _pick_fresh_topic()
        if topic:
            logger.info(f"[background] Smart idle triggered: topic='{topic}' ({elapsed / 3600:.1f}h)")
            await enqueue(QueueItem(
                type="idle_talk",
                handler=lambda t=topic: _smart_idle_speak(t),
                label=f"smart_idle: {topic}",
            ))
        else:
            logger.info("[background] Smart idle skipped: no fresh topics available")
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
    elif pick == "memory":
        prompt = _build_memory_idle_prompt()
        if prompt:
            logger.info(f"[background] Memory idle triggered ({elapsed / 3600:.1f}h)")
            await enqueue(QueueItem(
                type="idle_talk",
                handler=lambda p=prompt: _speak(p, "memory_idle"),
                label="memory_idle",
            ))
        else:
            # No memories yet — fall through to random
            _idle_record("random")
            prompt = _build_idle_random_prompt()
            logger.info(f"[background] Idle talk (random, memory fallback) triggered ({elapsed / 3600:.1f}h)")
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
            tz_name = action.get("timezone", "")  # empty → use the configured local tz

            if not fn_name or not target_time:
                continue

            # Resolve the action's timezone (falls back to the configured local tz)
            tz = ltime.get_tz()
            if tz_name:
                try:
                    from zoneinfo import ZoneInfo
                    tz = ZoneInfo(tz_name)
                except Exception:
                    pass
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
    today = ltime.now().strftime("%Y-%m-%d")
    for key in list(_scheduled_fired_today):
        if _scheduled_fired_today[key] != today:
            del _scheduled_fired_today[key]


_plugin_task_last_run: dict[str, float] = {}


async def _run_plugin_background_tasks(is_sleeping: bool):
    """Run plugin background tasks. Tasks with run_during_sleep=True run always.

    Honors manifest interval_seconds — tasks with intervals longer than the 60s
    loop are skipped until due (5s slack absorbs loop jitter).
    """
    tasks = plugin_loader.get_plugin_background_tasks()
    now = time.monotonic()
    for name, fn, run_during_sleep, interval in tasks:
        if is_sleeping and not run_during_sleep:
            continue
        last = _plugin_task_last_run.get(name)
        if last is not None and now - last < interval - 5:
            continue
        _plugin_task_last_run[name] = now
        try:
            await fn() if asyncio.iscoroutinefunction(fn) else fn()
        except Exception as e:
            logger.warning(f"[background] Plugin task {name} failed: {e}")


async def _main_loop():
    """Single background loop — checks both reminders and idle every 60s."""
    _load_daily_state()
    interval = config.BACKGROUND_POLL_SECONDS
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
            # Spending/calorie/task nags come from their plugins via background_check/idle
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
