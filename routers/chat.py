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

_COSTUME_TRIGGER = re.compile(
    r"\b(?:change|switch|swap|wear|put on|go|get into)\b",
    re.IGNORECASE,
)
_COSTUME_FILLER = re.compile(
    r"^(?:to|into|the|my|your|her|a)\s+",
    re.IGNORECASE,
)
_COSTUME_SUFFIX = re.compile(
    r"\s+(?:outfit|costume|clothes|please|pls)\b.*$",
    re.IGNORECASE,
)

_EXPENSE_PATTERNS = [
    re.compile(r"spent\s+(\d[\d.,]*k?)\s+(?:on|buying|for)\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"bought\s+(.+?)\s+(?:for|seharga)\s+(\d[\d.,]*k?)", re.IGNORECASE),
    re.compile(r"paid\s+(\d[\d.,]*k?)\s+for\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"beli\s+(.+?)\s+(?:seharga\s+)?(\d[\d.,]*k?)", re.IGNORECASE),
    re.compile(r"bayar\s+(\d[\d.,]*k?)\s+(?:untuk|buat)\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"spent\s+(\d[\d.,]*k?)\s+([a-zA-Z].+?)(?:\.|$)", re.IGNORECASE),
]

_INCOME_PATTERNS = [
    re.compile(r"(?:got\s+(?:paid|(?:my\s+)?paycheck|gaji)|gajian)\s*[,:]?\s*(\d[\d.,k]*)\s*(?:for\s+)?(.+)?", re.IGNORECASE),
    re.compile(r"(?:got|received)\s+(?:my\s+)?(.+?)\s+(?:for|of)\s+(\d[\d.,k]*)", re.IGNORECASE),
    re.compile(r"received\s+(\d[\d.,k]*)\s+(?:from\s+)?(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"(.+?)\s+(?:sent|gave|transferred)\s+(?:me\s+)?(\d[\d.,k]*)", re.IGNORECASE),
    re.compile(r"(?:salary|allowance|income|bonus|thr|uang\s+(?:jajan|saku|masuk))\s*[,:]?\s*(\d[\d.,k]*)\s*(?:from\s+)?(.+)?", re.IGNORECASE),
    re.compile(r"(?:got|found)\s+(?:random\s+)?money\s+(\d[\d.,k]*)\s*(.+)?", re.IGNORECASE),
]

_ADD_TASK_PATTERNS = [
    re.compile(r"remind\s+me\s+to\s+(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:add|create|new)\s+(?:task|todo|to-do)\s*[:\-]?\s*(.+?)(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:add)\s+(.+?)\s+to\s+(?:my\s+)?(?:list|tasks|todo)", re.IGNORECASE),
    re.compile(r"(?:i\s+need\s+to|i\s+have\s+to|i\s+should|gotta|harus)\s+(.+?)(?:\.|!|$)", re.IGNORECASE),
]

_COMPLETE_TASK_PATTERNS = [
    re.compile(r"(?:done\s+with|finished|completed|checked\s+off|selesai)\s+(.+?)(?:\.|!|$)", re.IGNORECASE),
    re.compile(r"i\s+(?:already|just)\s+(?:did|finished|completed)\s+(.+?)(?:\.|!|$)", re.IGNORECASE),
]

_WEATHER_PATTERN = re.compile(
    r"\b(?:weather|forecast|rain(?:ing|y)?|umbrella|sunny|cloudy|storm|cuaca|hujan)\b|"
    r"how(?:'s| is)\s+(?:it |the )?(?:outside|weather)|"
    r"(?:is it|will it)\s+(?:gonna |going to )?rain",
    re.IGNORECASE,
)
_BALANCE_PATTERN = re.compile(
    r"(?:how\s+much|berapa).{0,15}(?:have|left|money|saldo|duit|uang)|"
    r"(?:check|cek)\s*(?:my\s+)?(?:balance|saldo|budget|spending)|"
    r"\b(?:am I|apakah aku)\s+broke\b|"
    r"(?:my\s+)?(?:balance|saldo|spending\s+status)",
    re.IGNORECASE,
)
_CALENDAR_PATTERN = re.compile(
    r"(?:what(?:'s| is)|anything)\s+(?:on\s+)?(?:my\s+)?(?:schedule|calendar|agenda)|"
    r"(?:any|do I have)\s+(?:meetings?|appointments?|events?)|"
    r"(?:my|check)\s+(?:schedule|calendar|agenda)",
    re.IGNORECASE,
)
_LIST_TASKS_PATTERN = re.compile(
    r"(?:what(?:'s| is)|show|check)\s+(?:on\s+)?(?:my\s+)?(?:list|tasks?|todo|to-do)|"
    r"what\s+(?:do I|should I)\s+(?:need to|have to)\s+do|"
    r"(?:my|the)\s+(?:task\s*list|todo\s*list|to-do\s*list)",
    re.IGNORECASE,
)

_POLITICAL_MEME_PATTERN = re.compile(
    r"\b(?:jokowi|joko\s*widodo|jacobi|prabowo|prabhu|prabow|gibran|gibron|megawati|luhut|anies|baswedan|natalius|pigai|dpr|parlemen)\b",
    re.IGNORECASE,
)

_SAVE_MEMORY_PATTERN = re.compile(
    r"(?:note\s+this|remember\s+(?:this|that)|keep\s+in\s+mind|FYI|you\s+should\s+know)\s*[:\-]?\s*(.+?)(?:\.|$)|"
    r"(?:don'?t\s+forget)\s*[:\-]?\s*(.+?)(?:\.|$)",
    re.IGNORECASE,
)

_MEAL_PATTERNS = [
    re.compile(r"\b(?:ate|had|eaten|drank|drink)\s+(.+?)\s+for\s+(breakfast|lunch|dinner|snack)", re.IGNORECASE),
    re.compile(r"\b(?:ate|had|eaten|drank|drink)\s+(?:some\s+|an?\s+)?(.+?)(?:\s+(?:just now|tadi|barusan))?(?:\.|!|$)", re.IGNORECASE),
    re.compile(r"\b(?:makan|sarapan|minum)\s+(.+?)(?:\.|!|$)", re.IGNORECASE),
    re.compile(r"\b(?:breakfast|lunch|dinner|snack)\s*(?:was|is)?\s*(.+?)(?:\.|!|$)", re.IGNORECASE),
]
_CHECK_CALORIES_PATTERN = re.compile(
    r"(?:how\s+many|check|cek)\s*(?:my\s+)?(?:calories?|kalori|kcal|intake)|"
    r"(?:my\s+)?(?:calorie|kalori)\s+(?:count|status|total|intake)",
    re.IGNORECASE,
)

def _detect_costume_intent(user_message: str) -> str | None:
    trigger = _COSTUME_TRIGGER.search(user_message)
    if not trigger:
        return None
    after = user_message[trigger.end():].strip()
    after = _COSTUME_FILLER.sub("", after).strip()
    after = _COSTUME_SUFFIX.sub("", after).strip()
    if not after:
        return None
    return costume_registry.resolve(after)


def _parse_amount(raw: str) -> str | None:
    """Normalize amount string: '20k' -> '20000', '5.000' -> '5000'."""
    if not raw:
        return None
    raw = raw.strip().lower()
    multiplier = 1
    if raw.endswith("k"):
        multiplier = 1000
        raw = raw[:-1]
    raw = raw.replace(",", "").replace(".", "")
    if not raw.isdigit():
        return None
    return str(int(raw) * multiplier)


def _detect_expense_intent(user_message: str) -> str | None:
    for pattern in _EXPENSE_PATTERNS:
        match = pattern.search(user_message)
        if not match:
            continue
        g1, g2 = match.group(1), match.group(2)
        a1, a2 = _parse_amount(g1), _parse_amount(g2)
        if a1:
            amount, desc = a1, g2.strip()
        elif a2:
            amount, desc = a2, g1.strip()
        else:
            continue
        desc = re.sub(r"\s+(today|yesterday|just now|tadi|barusan).*$", "", desc, flags=re.IGNORECASE).strip()
        if not desc or not amount:
            continue
        return f"{amount}|{desc}"
    return None


def _detect_income_intent(user_message: str) -> str | None:
    for pattern in _INCOME_PATTERNS:
        match = pattern.search(user_message)
        if not match:
            continue
        g1, g2 = match.group(1), match.group(2) or ""
        a1, a2 = _parse_amount(g1), _parse_amount(g2)
        if a1:
            amount, desc = a1, g2.strip() or "income"
        elif a2:
            amount, desc = a2, g1.strip() or "income"
        else:
            continue
        desc = re.sub(r"\s+(today|yesterday|just now|tadi|barusan).*$", "", desc, flags=re.IGNORECASE).strip()
        if not amount:
            continue
        return f"{amount}|{desc or 'income'}"
    return None


def _detect_add_task_intent(user_message: str) -> str | None:
    for pattern in _ADD_TASK_PATTERNS:
        match = pattern.search(user_message)
        if match:
            title = match.group(1).strip().rstrip(".!,")
            if title and len(title) > 2:
                return title
    return None


def _detect_complete_task_intent(user_message: str) -> str | None:
    for pattern in _COMPLETE_TASK_PATTERNS:
        match = pattern.search(user_message)
        if match:
            title = match.group(1).strip().rstrip(".!,")
            if title and len(title) > 2:
                return title
    return None


def _detect_meal_intent(user_message: str) -> str | None:
    """Detect meal logging intent and extract meal_type|food_name."""
    for pattern in _MEAL_PATTERNS:
        match = pattern.search(user_message)
        if not match:
            continue
        groups = match.groups()
        if len(groups) >= 2:
            food = groups[0].strip().rstrip(".!,")
            meal_type = groups[1].lower()
        else:
            food = groups[0].strip().rstrip(".!,")
            meal_type = "snack"
        if food and len(food) > 1:
            return f"{meal_type}|{food}"
    return None


def _detect_tool_fallback(user_message: str) -> tuple[str | None, str | None]:
    """Detect tool intent via keywords when LLM doesn't generate a tool tag.
    Returns (tool_name, tool_arg) or (None, None).
    """
    matched = tool_router.get_relevant_tools(user_message)

    # Expense detection
    if "add_expense" in matched:
        arg = _detect_expense_intent(user_message)
        if arg:
            return "add_expense", arg

    # Income detection
    if "add_income" in matched:
        arg = _detect_income_intent(user_message)
        if arg:
            return "add_income", arg

    # Balance check (no arg)
    if "check_balance" in matched:
        if _BALANCE_PATTERN.search(user_message):
            return "check_balance", ""

    # Task management
    if "complete_task" in matched:
        arg = _detect_complete_task_intent(user_message)
        if arg:
            return "complete_task", arg

    if "add_task" in matched:
        arg = _detect_add_task_intent(user_message)
        if arg:
            return "add_task", arg

    if "list_tasks" in matched:
        if _LIST_TASKS_PATTERN.search(user_message):
            return "list_tasks", ""

    # Weather (no arg)
    if "check_weather" in matched:
        if _WEATHER_PATTERN.search(user_message):
            return "check_weather", ""

    # Calendar (no arg)
    if "check_calendar" in matched:
        if _CALENDAR_PATTERN.search(user_message):
            return "check_calendar", ""

    # Calorie tracking
    if "check_calories" in matched:
        if _CHECK_CALORIES_PATTERN.search(user_message):
            return "check_calories", ""

    if "add_meal" in matched:
        arg = _detect_meal_intent(user_message)
        if arg:
            return "add_meal", arg

    # Costume detection
    costume = _detect_costume_intent(user_message)
    if costume:
        return "change_costume", costume

    # Political meme fallback
    if "get_political_meme" in matched:
        meme_match = _POLITICAL_MEME_PATTERN.search(user_message)
        if meme_match:
            return "get_political_meme", meme_match.group(0).lower()

    # Memory save fallback
    if "save_memory" in matched:
        mem_match = _SAVE_MEMORY_PATTERN.search(user_message)
        if mem_match:
            content = (mem_match.group(1) or mem_match.group(2) or "").strip()
            if content:
                return "save_memory", f"fact|{content}"

    # Entertainment tools — always trigger when keywords match (no args needed)
    if "open_gacha" in matched:
        return "open_gacha", ""
    if "spin_roulette" in matched and _roulette_active():
        return "spin_roulette", ""
    if "open_roulette" in matched:
        return "open_roulette", ""
    if "give_thr" in matched:
        return "give_thr", ""

    return None, None


def _roulette_active() -> bool:
    """Check if roulette is currently open (for spin fallback)."""
    try:
        from services.tools.roulette_tool import _roulette_active as active, _roulette_timestamp, _ROULETTE_TIMEOUT
        import time
        return active and (time.time() - _roulette_timestamp) < _ROULETTE_TIMEOUT
    except Exception:
        return False

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

    # Store exchange in persistent memory
    memory.add_message("user", req.message)
    memory.add_message("assistant", result["reply"], emotion=result["emotion"])

    # Mood: react to user sentiment and character's own emotion
    if config.MOOD_ENABLED:
        user_sentiment = mood.detect_user_sentiment(req.message)
        mood.on_emotion_response(result["emotion"], user_sentiment=user_sentiment)

    tool_executed = None

    # Execute tool if the LLM requested one
    tool_name = result.get("tool_name")
    tool_arg = result.get("tool_arg")

    # Fallback: keyword-based tool detection when LLM misses
    # Claude text-tag mode also needs this — Haiku often skips [TOOL:] tags
    if not tool_name and config.LLM_PROVIDER in ("lmstudio", "ollama", "claude"):
        fb_tool, fb_arg = _detect_tool_fallback(req.message)
        if fb_tool:
            tool_name = fb_tool
            tool_arg = fb_arg
            logger.info(f"[chat] Keyword fallback triggered: {fb_tool}({fb_arg})")

    costume_change_id = None
    tool_result = None

    if tool_name and config.MOOD_ENABLED and mood.should_disobey():
        logger.info(f"[chat] Mood disobey! Skipping tool '{tool_name}' (mood={mood.get_mood():.0f})")
        tool_name = None
        tool_arg = None

    if tool_name:
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
                else:
                    if manager.client_count > 0:
                        for effect in tool_result.get("side_effects", []):
                            await manager.broadcast(effect)
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
