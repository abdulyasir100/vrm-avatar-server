"""RSS plugin — FreshRSS feed monitoring and character post detection."""

import logging
import random
from services import freshrss, ntfy
from services.message_queue import QueueItem, enqueue
import config

logger = logging.getLogger(__name__)

_seen_posts: set[str] = set()
_seeded: bool = False


async def _speak(prompt: str, context: str):
    """Run prompt through LLM → TTS → broadcast."""
    from services import llm, tts_service, memory, sticker as sticker_svc
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
            "type": "chat", "reply": reply, "emotion": emotion,
            "audio_url": audio_url, "context": context, "user_name": "System",
        })

    await ntfy.notify(title=config.CHARACTER_NAME, message=reply)

    if config.STICKER_ENABLED and random.random() < config.STICKER_CHANCE:
        sticker_id = sticker_svc.resolve(reply, emotion)
        if sticker_id:
            await ntfy.send_sticker(sticker_id)

    chat_logger = logging.getLogger("chat")
    chat_logger.info(f"[{context}] {config.CHARACTER_NAME} [{emotion}]: {reply}")


async def background_tick():
    """Check for new character posts in RSS feeds."""
    global _seen_posts, _seeded

    if not config.FRESHRSS_ENABLED:
        return

    try:
        new_posts = await freshrss.get_new_character_posts(_seen_posts)
    except Exception:
        return

    if not _seeded:
        _seeded = True
        for post in new_posts:
            _seen_posts.add(post.get("id", post["title"]))
        if new_posts:
            logger.info(f"[rss] Seeded {len(new_posts)} existing character posts")
        return

    for post in new_posts[:1]:
        _seen_posts.add(post.get("id", post["title"]))
        prompt = (
            f'You ({config.CHARACTER_NAME}) just posted something! '
            f'Title: "{post["title"]}" on {post["source"]}. '
            f'Excitedly tell your user about it. One sentence.'
        )
        logger.info(f"[rss] New character post detected: {post['title']}")

        msg_id = await ntfy.notify(
            title=f"{config.CHARACTER_NAME} posted!",
            message=post["title"],
            priority=4,
            tags=["star"],
        )
        if msg_id:
            await ntfy.pin_message(msg_id)

        await enqueue(QueueItem(
            type="character_post",
            handler=lambda p=prompt: _speak(p, "character_post"),
            label=f"post: {post['title'][:30]}",
        ))

        from services import background
        background.reset_idle_timer()


async def cmd_headlines(args: str = "") -> dict:
    """Show recent RSS headlines."""
    if not config.FRESHRSS_ENABLED:
        return {"text": "FreshRSS not configured.", "inline_keyboard": None}

    articles = await freshrss.fetch_recent_articles(count=10)
    if not articles:
        return {"text": "No recent headlines.", "inline_keyboard": None}

    lines = ["📰 Recent Headlines"]
    for a in articles[:10]:
        marker = " ⭐" if a.get("is_character_post") else ""
        lines.append(f"• {a['title'][:50]}{marker}")

    return {"text": "\n".join(lines), "inline_keyboard": None}
