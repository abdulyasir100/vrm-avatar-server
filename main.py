"""Avatar AI Server — FastAPI application."""

import asyncio
import logging
import logging.handlers
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from services import llm, tts_service, stt_service, rvc_service, tool_registry, memory, tool_router, costume_registry, background, message_queue, mood, sticker, plugin_loader
import services.tools
from routers import status, chat, tts, stt, ws, event, costume, memory_router, weather, prayer, admin, stickers, plugin as plugin_router, dashboard, phone_event, trading
import config

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)
_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Silence noisy third-party loggers — these flood all.log with
# every Telegram getMe poll and HuggingFace download URL otherwise.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

_all_handler = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "all.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8",
)
_all_handler.setLevel(logging.INFO)
_all_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
logging.getLogger().addHandler(_all_handler)

_err_handler = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "error.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8",
)
_err_handler.setLevel(logging.WARNING)
_err_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
logging.getLogger().addHandler(_err_handler)

chat_logger = logging.getLogger("chat")
_chat_handler = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "chat.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8",
)
_chat_handler.setLevel(logging.INFO)
_chat_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
chat_logger.addHandler(_chat_handler)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Avatar AI Server",
    description="LLM chat + TTS + WebSocket relay for VRM avatar companion",
    version="0.1.0",
)

app.include_router(status.router)
app.include_router(chat.router)
app.include_router(tts.router)
app.include_router(stt.router)
app.include_router(ws.router)
app.include_router(event.router)
app.include_router(costume.router)
app.include_router(memory_router.router)
app.include_router(weather.router)
app.include_router(prayer.router)
app.include_router(admin.router)
app.include_router(stickers.router)
app.include_router(plugin_router.router)
app.include_router(dashboard.router)
app.include_router(phone_event.router)
app.include_router(trading.router)

audio_dir = Path("audio")
audio_dir.mkdir(exist_ok=True)
app.mount("/audio", StaticFiles(directory="audio"), name="audio")

@app.on_event("startup")
async def startup():
    logger.info("=" * 50)
    config.load_character()
    # Reapply persisted /settings (survives container rebuilds) before services
    # that read these values (TTS, idle loop, tool mode) initialize below.
    from services import runtime_settings
    runtime_settings.apply()
    logger.info("Avatar AI Server starting...")
    logger.info(f"Character: {config.CHARACTER_NAME} (nicknames: {config.CHARACTER_NICKNAMES})")
    logger.info(f"Port: {config.AVATAR_SERVER_PORT}")
    logger.info(f"LLM Provider: {config.LLM_PROVIDER}")
    logger.info(f"LLM Model: {config.get_llm_model()}")
    logger.info("=" * 50)

    memory.init(config.MEMORY_DB_PATH)
    memory.cleanup_old_conversations(config.MEMORY_CLEANUP_DAYS)
    msg_count = memory.get_message_count()
    logger.info(f"Memory: {msg_count} messages stored (session: {memory.get_current_session_id()})")

    mood.init(config.MEMORY_DB_PATH)
    logger.info(f"Mood: {mood.get_mood():.0f}/100 ({mood.get_bracket()})")

    costume_registry.load()
    sticker.load()
    logger.info(f"Stickers: {'enabled' if config.STICKER_ENABLED else 'disabled'} ({len(sticker.get_all())} loaded)")

    llm.load_system_prompt()

    tool_router.load_tool_docs("prompts/tools")
    tool_router.setup_default_routes()

    plugin_loader.load_all_plugins()
    tool_router.register_plugin_routes()

    logger.info(f"Tool router: {len(tool_router._tool_docs)} tool docs, {len(tool_router._routes)} routes")

    tools = tool_registry.list_tools()
    logger.info(f"Tools: {len(tools)} registered — {tools}")

    lm_status = await llm.check_llm()
    if lm_status == "ok":
        logger.info(f"LLM ({config.LLM_PROVIDER}): CONNECTED")
    else:
        logger.warning(f"LLM ({config.LLM_PROVIDER}): OFFLINE — /chat will return fallback responses")

    if config.TTS_ENABLED:
        ok = tts_service.init_tts()
        logger.info(f"TTS: {'READY' if ok else 'FAILED — /tts will return 503'}")
    else:
        logger.info("TTS: disabled")

    if config.RVC_ENABLED:
        ok = rvc_service.init_rvc()
        logger.info(f"RVC: {'READY' if ok else 'FAILED — will use Kokoro voice directly'}")
    else:
        logger.info("RVC: disabled")

    if config.STT_ENABLED:
        ok = stt_service.init_stt()
        logger.info(f"STT: {'READY' if ok else 'FAILED — /transcribe will return 503'}")
    else:
        logger.info("STT: disabled")

    message_queue.start()

    from services import ntfy, freshrss, calendar_service, prayer as prayer_svc
    ntfy_status = await ntfy.check_health()
    freshrss_status = await freshrss.check_health()
    calendar_status = await calendar_service.check_health()
    prayer_status = await prayer_svc.check_health()
    logger.info(f"Telegram Notify: {ntfy_status.upper()}")
    logger.info(f"FreshRSS: {freshrss_status.upper()}")
    logger.info(f"Calendar: {calendar_status.upper()}")
    logger.info(f"Prayer Times: {prayer_status.upper()}")

    background.start()
    logger.info(f"Background: idle talk every {config.IDLE_TALK_INTERVAL_HOURS}h, reminders on the 60s loop")

    asyncio.create_task(llm.prime_phone_context())

    from services import user_state
    asyncio.create_task(user_state.refresh_loop())
    logger.info("User state: refresh loop scheduled")


@app.get("/")
async def root():
    return {"name": "Avatar AI Server", "version": "0.1.0", "status": "running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.AVATAR_SERVER_HOST,
        port=config.AVATAR_SERVER_PORT,
        reload=True,
    )
