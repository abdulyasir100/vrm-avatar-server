"""POST /transcribe — Speech-to-text endpoint."""

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from services import stt_service
import config

router = APIRouter()


class TranscribeResponse(BaseModel):
    text: str
    language: Optional[str] = None


@router.post("/transcribe", response_model=TranscribeResponse)
async def post_transcribe(file: UploadFile = File(...)):
    """
    Transcribe an audio file to text.
    Accepts: WAV, MP3, OGG, WebM, M4A.
    Returns the transcribed text.
    """
    if not config.STT_ENABLED:
        raise HTTPException(status_code=503, detail="STT is disabled")

    if not stt_service.is_ready():
        raise HTTPException(status_code=503, detail="STT model not loaded")

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    content_type = file.content_type or "audio/wav"
    text, language = stt_service.transcribe(audio_bytes, content_type)

    if text is None:
        raise HTTPException(status_code=500, detail="Transcription failed or audio was silent")

    return TranscribeResponse(text=text, language=language)
