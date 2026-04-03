"""POST /tts — Text-to-speech endpoint."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from services import tts_service
import config

router = APIRouter()


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    speed: Optional[float] = 1.0
    emotion: Optional[str] = "neutral"


class TTSResponse(BaseModel):
    audio_url: str
    voice: str


@router.post("/tts", response_model=TTSResponse)
async def post_tts(req: TTSRequest):
    """Synthesize text to speech. Returns a URL to the generated WAV file."""
    if not config.TTS_ENABLED:
        raise HTTPException(status_code=503, detail="TTS is disabled")

    if not tts_service.is_ready():
        raise HTTPException(status_code=503, detail="TTS model not loaded")

    audio_url = tts_service.synthesize(
        text=req.text,
        voice=req.voice,
        speed=req.speed or 1.0,
        emotion=req.emotion or "neutral",
    )

    if not audio_url:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")

    return TTSResponse(audio_url=audio_url, voice=req.voice or config.TTS_VOICE)
