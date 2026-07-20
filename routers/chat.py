"""POST /chat — LLM conversation endpoint.

Pipeline split:
  - LLM + tool execution: runs immediately, returns HTTP response
  - TTS + WebSocket broadcast: enqueued, processed sequentially
"""

import re
import logging
import asyncio
import uuid
import base64
import os
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services import llm, tts_service, tool_registry, memory, costume_registry, background, sleep, mood, sticker, ntfy
from services.ws_manager import manager
from services import tool_router
import config

logger = logging.getLogger(__name__)
chat_logger = logging.getLogger("chat")
router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    context: Optional[str] = "telegram"
    user_name: Optional[str] = "User"
    image_base64: Optional[str] = None
    reply_to: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    emotion: str
    audio_url: Optional[str] = None
    tool_executed: Optional[str] = None
    sticker_id: Optional[str] = None


@router.post("/chat", response_model=ChatResponse)
async def post_chat(req: ChatRequest):
    """Send a message to the LLM and get a response with emotion."""
    background.reset_idle_timer()

    # --- /mood command: force-set mood value ---
    mood_cmd = re.match(r"^/mood\s+(\d+)", req.message.strip())
    if mood_cmd and config.MOOD_ENABLED:
        target = max(0, min(100, int(mood_cmd.group(1))))
        delta = target - mood.get_mood()
        mood.adjust(delta, "admin_command")
        reply = f"Mood set to {target}. ({mood.get_bracket()})"
        if manager.client_count > 0:
            await manager.broadcast({"type": "mood", "value": mood.get_mood()})
        return ChatResponse(reply=reply, emotion="NEUTRAL")

    # --- Wake up if sleeping ---
    was_sleeping = False
    if sleep.is_sleeping():
        was_sleeping = sleep.wake_up("user_chat")
        if was_sleeping and manager.client_count > 0:
            await manager.broadcast({"type": "sleep", "sleeping": False})

    llm_message = req.message
    if was_sleeping:
        llm_message = (
            "[SYSTEM: You were just woken up from sleep. You are groggy and annoyed. "
            "Start with something like 'Mmh... what?' or 'I was sleeping...'. "
            "If there's something to do, do it but be grumpy. "
            "If they're just chatting, tell them to go sleep. Very short reply.]\n\n"
            + req.message
        )

    # Save image to temp file if provided
    image_path = None
    if req.image_base64:
        try:
            img_bytes = base64.b64decode(req.image_base64)
            image_path = f"/tmp/avatar_img_{uuid.uuid4().hex[:8]}.png"
            with open(image_path, "wb") as f:
                f.write(img_bytes)
            logger.info(f"[chat] Saved image to {image_path} ({len(img_bytes)} bytes)")
        except Exception as e:
            logger.error(f"[chat] Failed to save image: {e}")
            image_path = None

    try:
        result = await llm.chat(
            message=llm_message,
            context=req.context or "telegram",
            user_name=req.user_name or "User",
            image_path=image_path,
            reply_to=req.reply_to,
        )
    finally:
        # Clean up temp image
        if image_path:
            try:
                os.unlink(image_path)
                logger.info(f"[chat] Cleaned up {image_path}")
            except OSError:
                pass

    # Store user message now; assistant reply is stored after the tool-result
    # merge below, so history never holds an empty turn when the LLM emits a
    # bare [TOOL:] tag and the tool text becomes the reply.
    memory.add_message("user", req.message)

    # Mood: react to user sentiment and character's own emotion
    if config.MOOD_ENABLED:
        user_sentiment = mood.detect_user_sentiment(req.message)
        mood.on_emotion_response(result["emotion"], user_sentiment=user_sentiment)

    tool_executed = None

    # Execute tool if the LLM requested one
    tool_name = result.get("tool_name")
    tool_arg = result.get("tool_arg")

    # Claude Agent SDK path: tools already ran natively inside the agentic loop.
    # result["sdk_tools_ran"] is a list of {tool, arg, result}. Don't re-execute or
    # keyword-fallback — just merge the executed results into the shape the
    # downstream broadcast/TTS code expects.
    sdk_ran = result.get("sdk_tools_ran")
    _used_sdk = sdk_ran is not None

    costume_change_id = None
    tool_result = None

    # Tool calls come from the LLM (native SDK tool calls or [TOOL:] text tags).
    # No engine-side keyword fallback — plugins declare their own triggers.

    if _used_sdk and sdk_ran:
        # Merge SDK-executed tool results. Costume effects route through
        # costume_change_id; other side_effects (gacha/roulette/thr) broadcast
        # after the chat audio frame. result is "" so the reply isn't re-appended
        # (the model already spoke the outcome) — except the meme special-case.
        merged_side_effects = []
        skip_tts_any = skip_reply_any = False
        meme_text = None
        non_costume_tool = None
        for entry in sdk_ran:
            res = entry.get("result") or {}
            skip_tts_any = skip_tts_any or res.get("skip_tts", False)
            skip_reply_any = skip_reply_any or res.get("skip_reply", False)
            for effect in res.get("side_effects", []):
                if effect.get("type") == "costume":
                    if res.get("ok"):
                        costume_change_id = effect.get("costume_id")
                else:
                    merged_side_effects.append(effect)
            if entry["tool"] != "change_costume":
                non_costume_tool = entry["tool"]
            if entry["tool"] == "get_political_meme" and res.get("result"):
                meme_text = res["result"]
        tool_executed = non_costume_tool or sdk_ran[-1]["tool"]
        tool_result = {
            "result": meme_text if meme_text else "",
            "side_effects": merged_side_effects,
            "skip_tts": skip_tts_any,
            "skip_reply": skip_reply_any,
        }
        if meme_text:
            tool_executed = "get_political_meme"
        logger.info(f"[chat] SDK tools ran: {[e['tool'] for e in sdk_ran]}")

    if tool_name and config.MOOD_ENABLED and mood.should_disobey():
        logger.info(f"[chat] Mood disobey! Skipping tool '{tool_name}' (mood={mood.get_mood():.0f})")
        tool_name = None
        tool_arg = None

    if not _used_sdk and tool_name:
        handler = tool_registry.get(tool_name)
        if handler:
            try:
                tool_result = await handler(tool_arg or "", {
                    "context": req.context,
                    "user_name": req.user_name,
                })
                logger.info(f"[chat] Tool '{tool_name}' result: {tool_result}")
                tool_executed = tool_name

                if tool_name == "change_costume" and tool_result.get("ok"):
                    for effect in tool_result.get("side_effects", []):
                        if effect.get("type") == "costume":
                            costume_change_id = effect.get("costume_id")
                # Non-costume side_effects (gacha/roulette/thr animations) are
                # broadcast AFTER the chat audio broadcast below — see end of
                # this function — so the character's intro speech reaches Unity before
                # the animation message does.
            except Exception as e:
                logger.error(f"[chat] Tool '{tool_name}' failed: {e}")
        else:
            logger.warning(f"[chat] LLM requested unknown tool: {tool_name}")

    # --- Merge tool result into reply ---
    reply = result["reply"]
    emotion = result["emotion"]

    # Guard: if LLM returned empty/dots-only, use a fallback
    if not reply and not result.get("tool_name"):
        reply = "Hmm, I don't have anything useful for that."
        emotion = config.DEFAULT_EMOTION

    if tool_executed and tool_result:
        tool_text = tool_result.get("result", "")
        if tool_text:
            if tool_executed == "get_political_meme":
                # Meme tool: just use the meme, skip LLM blabber
                reply = tool_text
            elif not reply:
                # LLM gave empty text — use tool result as reply
                reply = tool_text
            else:
                # Append tool result so data isn't lost (e.g. balance, weather)
                reply = f"{reply}\n\n{tool_text}"

    memory.add_message("assistant", reply, emotion=emotion)

    chat_logger.info(f"[{req.context}] {req.user_name}: {req.message}")
    chat_logger.info(f"[{req.context}] {config.CHARACTER_NAME} [{emotion}]: {reply}")
    if tool_executed:
        chat_logger.info(f"[{req.context}] Tool: {tool_executed}({tool_arg})")

    # --- TTS + broadcast: skip TTS if no avatar client connected (saves VRAM/time) ---
    # Also skip if tool explicitly says so (e.g. gacha/roulette/THR tablet animations)
    skip_tts = tool_result.get("skip_tts", False) if tool_result else False
    skip_reply = tool_result.get("skip_reply", False) if tool_result else False
    if skip_reply:
        skip_tts = True
        reply = ""
    # Bilingual parsing: when JP mode is on, extract en/jp lines
    tts_text = reply
    if config.TTS_LANGUAGE == "jp" and reply:
        en_text, jp_text = llm.parse_bilingual(reply)
        if jp_text:
            tts_text = jp_text  # Japanese text for TTS
            reply = en_text     # English text for subtitle + Telegram

    audio_url = None
    sentences = []
    if config.TTS_ENABLED and tts_service.is_ready() and tts_text and manager.client_count > 0 and not skip_tts:
        logger.info(f"[chat] TTS input: lang={config.TTS_LANGUAGE} emotion={emotion} text={tts_text[:80]}...")

        sentences = tts_service.split_sentences(tts_text)
        if len(sentences) > 1:
            # Multi-sentence: synthesize first, send immediately, stream rest
            try:
                audio_url = await asyncio.to_thread(
                    tts_service.synthesize_single, sentences[0], emotion.lower()
                )
                logger.info(f"[chat] TTS first chunk ready ({len(sentences)} sentences)")
            except Exception as e:
                logger.error(f"[chat] TTS first chunk failed: {e}")
        else:
            # Single sentence: synthesize normally
            try:
                audio_url = await asyncio.to_thread(
                    tts_service.synthesize, tts_text, None, 1.0, emotion.lower()
                )
            except Exception as e:
                logger.error(f"[chat] TTS failed: {e}")

    # Broadcast to Tab (text + first audio) — skip for tablet-animation tools
    if manager.client_count > 0 and not skip_tts:
        payload = {
            "type": "chat",
            "reply": reply,
            "emotion": emotion,
            "audio_url": audio_url,
            "context": req.context,
            "user_name": req.user_name,
        }
        if costume_change_id:
            payload["costume_id"] = costume_change_id
        await manager.broadcast(payload)

        # Broadcast non-costume side_effects (gacha/roulette/thr animations)
        # AFTER the chat audio frame so the character's intro speech reaches Unity first.
        if tool_executed and tool_executed != "change_costume" and tool_result:
            for effect in tool_result.get("side_effects", []):
                await manager.broadcast(effect)

        # Stream remaining sentences in background
        if config.TTS_ENABLED and len(sentences) > 1:
            async def _stream_rest():
                for i, sentence in enumerate(sentences[1:], 2):
                    try:
                        chunk_url = await asyncio.to_thread(
                            tts_service.synthesize_single, sentence, emotion.lower()
                        )
                        if chunk_url:
                            await manager.broadcast({
                                "type": "audio_continue",
                                "audio_url": chunk_url,
                                "chunk": i,
                                "total": len(sentences),
                            })
                            logger.info(f"[chat] TTS chunk {i}/{len(sentences)} sent")
                    except Exception as e:
                        logger.error(f"[chat] TTS chunk {i} failed: {e}")
            asyncio.create_task(_stream_rest())

    # Broadcast mood update to Unity
    if config.MOOD_ENABLED and manager.client_count > 0:
        await manager.broadcast({"type": "mood", "value": mood.get_mood()})

    # Resolve sticker
    resolved_sticker = None
    if config.STICKER_ENABLED:
        resolved_sticker = sticker.resolve(reply, emotion)

    # Push to Telegram for non-telegram contexts (e.g. tablet touch)
    if req.context != "telegram" and reply:
        await ntfy.notify(title=config.CHARACTER_NAME, message=reply)
        if config.STICKER_ENABLED and resolved_sticker:
            await ntfy.send_sticker(resolved_sticker)

    return ChatResponse(
        reply=reply,
        emotion=emotion,
        audio_url=audio_url,
        tool_executed=tool_executed,
        sticker_id=resolved_sticker,
    )
