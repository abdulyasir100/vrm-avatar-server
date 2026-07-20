"""STT Service — faster-whisper speech-to-text."""

import logging
import tempfile
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_model = None
_stt_ready = False

def init_stt() -> bool:
    """Load Whisper model. Called once at startup. Returns True if successful."""
    global _model, _stt_ready

    import config

    try:
        from faster_whisper import WhisperModel
        model_size = config.STT_MODEL_SIZE
        logger.info(f"[stt] Loading Whisper model '{model_size}' on CPU (int8)...")
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _stt_ready = True
        logger.info("[stt] Whisper ready (CPU/int8)")
        return True
    except Exception as e:
        logger.error(f"[stt] Failed to load Whisper: {e}")
        _stt_ready = False
        return False


def is_ready() -> bool:
    return _stt_ready


def transcribe(audio_bytes: bytes, content_type: str = "audio/wav") -> tuple[Optional[str], Optional[str]]:
    """
    Transcribe audio bytes to text.
    Accepts WAV, MP3, OGG, WebM, etc.
    Returns (text, language) tuple or (None, None) on failure.
    """
    if not _stt_ready or _model is None:
        logger.warning("[stt] STT not ready")
        return None, None

    ext_map = {
        "audio/wav": ".wav",
        "audio/wave": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "audio/webm": ".webm",
        "audio/mp4": ".m4a",
    }
    ext = ext_map.get(content_type.split(";")[0].strip(), ".wav")

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, info = _model.transcribe(
            tmp_path,
            beam_size=5,
            language=None,
            vad_filter=False,
        )

        text = " ".join(seg.text.strip() for seg in segments).strip()
        detected_lang = info.language
        logger.info(f"[stt] Transcribed ({detected_lang}): {text[:80]}")
        return (text, detected_lang) if text else (None, None)

    except Exception as e:
        logger.error(f"[stt] Transcription failed: {e}")
        return None, None
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
