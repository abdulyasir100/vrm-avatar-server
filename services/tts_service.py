"""TTS Service — supports OmniVoice, CosyVoice 3, Qwen3-TTS, HF Space, Kokoro-ONNX.

Engine priority (set via TTS_ENGINE env var):
  - omnivoice: PRIMARY — k2-fsa/OmniVoice (RTX 4070 PC :9192), 600+ langs,
               ~4x faster than CV3, emotion via pitch/whisper whitelist
  - cosyvoice: CV3 with inference_instruct2 (RTX 4070 PC :9191) — kept for rollback
  - qwen3tts:  legacy chichi-speech (RTX 4070 PC :9090)
  - hfspace:   cloud GPU via HuggingFace ZeroGPU
  - kokoro:    CPU-only ONNX, fast but generic voice (always loaded as fallback)
"""

import logging
import uuid
import wave
import re
import numpy as np
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded instances
_kokoro = None
_hf_client = None
_tts_ready = False
_kokoro_ready = False  # track kokoro separately for fallback
_engine = "kokoro"  # "qwen3tts", "hfspace", or "kokoro"


def init_tts() -> bool:
    """Load TTS engine. Called once at startup. Returns True if successful."""
    global _tts_ready, _engine, _kokoro_ready

    import config
    _engine = getattr(config, "TTS_ENGINE", "kokoro")

    if _engine == "omnivoice":
        ok = _init_omnivoice()
        _kokoro_ready = _init_kokoro()
        if _kokoro_ready:
            logger.info("[tts] Kokoro ready as fallback for OmniVoice")
        return ok or _kokoro_ready
    elif _engine == "cosyvoice":
        ok = _init_cosyvoice()
        _kokoro_ready = _init_kokoro()
        if _kokoro_ready:
            logger.info("[tts] Kokoro ready as fallback for CosyVoice")
        return ok or _kokoro_ready
    elif _engine == "qwen3tts":
        ok = _init_qwen3tts()
        _kokoro_ready = _init_kokoro()
        if _kokoro_ready:
            logger.info("[tts] Kokoro ready as fallback for Qwen3-TTS")
        return ok or _kokoro_ready
    elif _engine == "hfspace":
        ok = _init_hf_space()
        _kokoro_ready = _init_kokoro()
        if _kokoro_ready:
            logger.info("[tts] Kokoro ready as fallback for HF Space")
        return ok or _kokoro_ready
    else:
        return _init_kokoro()


def _init_omnivoice() -> bool:
    """Check if OmniVoice server is reachable."""
    global _tts_ready
    import config
    import httpx
    try:
        resp = httpx.get(f"{config.OMNIVOICE_API_URL}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"[tts] OmniVoice ready at {config.OMNIVOICE_API_URL} (CUDA={data.get('cuda')})")
            _tts_ready = True
            return True
    except Exception:
        pass
    logger.warning(f"[tts] OmniVoice API not reachable at {config.OMNIVOICE_API_URL}. Will use Kokoro fallback.")
    return False


def _init_cosyvoice() -> bool:
    """Check if CosyVoice 3 server is reachable."""
    global _tts_ready
    import config
    import httpx
    try:
        resp = httpx.get(f"{config.COSYVOICE_API_URL}/health", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"[tts] CosyVoice 3 ready at {config.COSYVOICE_API_URL} (CUDA={data.get('cuda')})")
            _tts_ready = True
            return True
    except Exception:
        pass
    logger.warning(f"[tts] CosyVoice API not reachable at {config.COSYVOICE_API_URL}. Will use Kokoro fallback.")
    return False


def _init_kokoro() -> bool:
    """Initialize Kokoro-ONNX TTS."""
    global _kokoro, _tts_ready

    import config
    model_path = Path(config.TTS_MODEL_PATH)
    voices_path = Path("models/voices-v1_0.bin")

    if not model_path.exists():
        logger.error(f"[tts] Model not found: {model_path}")
        return False
    if not voices_path.exists():
        logger.error(f"[tts] Voices bundle not found: {voices_path}")
        return False

    try:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(model_path), str(voices_path))
        _tts_ready = True
        voices = _kokoro.get_voices()
        logger.info(f"[tts] Kokoro ready — model={model_path.name}, voice={config.TTS_VOICE}, available voices: {len(voices)}")
        return True
    except Exception as e:
        logger.error(f"[tts] Failed to load Kokoro: {e}")
        _tts_ready = False
        return False


def _init_qwen3tts() -> bool:
    """Check Qwen3-TTS (chichi-speech) API is reachable."""
    global _tts_ready

    import config
    import requests

    api_url = config.QWEN3TTS_API_URL

    try:
        r = requests.get(f"{api_url}/docs", timeout=5)
        _tts_ready = True
        logger.info(f"[tts] Qwen3-TTS ready — api={api_url}")
        return True
    except requests.ConnectionError:
        logger.error(f"[tts] Qwen3-TTS API not reachable at {api_url}. Start chichi-speech: python -m chichi_speech.server --host 127.0.0.1 --port 9090")
        return False
    except Exception as e:
        logger.error(f"[tts] Qwen3-TTS init error: {e}")
        return False



def _init_hf_space() -> bool:
    """Initialize HuggingFace Space TTS client."""
    global _hf_client, _tts_ready

    import config

    try:
        from gradio_client import Client
        space_url = config.HF_SPACE_URL
        logger.info(f"[tts] Connecting to HF Space: {space_url}...")
        _hf_client = Client(space_url)
        _tts_ready = True
        logger.info(f"[tts] HF Space TTS ready — space={space_url}")
        return True
    except Exception as e:
        logger.error(f"[tts] HF Space init failed: {e}")
        return False



def is_ready() -> bool:
    return _tts_ready


def _cleanup_audio_dir():
    """Keep only the N most recent WAV files in audio/, delete the rest."""
    import config

    audio_dir = Path("audio")
    if not audio_dir.exists():
        return

    keep = getattr(config, "AUDIO_KEEP_COUNT", 20)
    wav_files = sorted(audio_dir.glob("*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)

    for old_file in wav_files[keep:]:
        try:
            old_file.unlink()
            logger.debug(f"[tts] Cleaned up old audio: {old_file.name}")
        except Exception as e:
            logger.warning(f"[tts] Failed to delete {old_file.name}: {e}")


def split_sentences(text: str, min_chunk_chars: int = 40) -> list[str]:
    """Split text into sentences for chunked TTS synthesis.

    Merges short sentences with neighbors so each chunk is at least
    min_chunk_chars long. This avoids tiny fragments like "What?" being
    synthesized alone.
    """
    import re
    # Split on sentence-ending punctuation followed by space or end
    parts = re.split(r'(?<=[.!?。！？])\s+', text.strip())
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return [text.strip()]

    # Merge short fragments into bigger chunks
    sentences = []
    current = parts[0]
    for p in parts[1:]:
        if len(current) < min_chunk_chars:
            current += " " + p
        else:
            sentences.append(current)
            current = p
    sentences.append(current)

    return sentences


def _resolve_neutral_emotion(emotion: str) -> str:
    """Replace NEUTRAL with a mood-based emotion so TTS never sounds flat."""
    if emotion.upper() != "NEUTRAL":
        return emotion
    try:
        from services import mood
        bracket = mood.get_bracket()
        mapping = {
            "adoring":   "HAPPY",
            "normal":    "HAPPY",
            "sassy":     "ANGRY",
            "reluctant": "SAD",
            "defiant":   "ANGRY",
        }
        resolved = mapping.get(bracket, "HAPPY")
        logger.debug(f"[tts] Neutral emotion → {resolved} (mood: {bracket})")
        return resolved
    except Exception:
        return "HAPPY"


def synthesize_single(text: str, emotion: str = "neutral") -> Optional[str]:
    """Synthesize a single sentence/chunk to a WAV file. Returns audio URL or None."""
    import config
    if not _tts_ready:
        return None

    emotion = _resolve_neutral_emotion(emotion)

    clean = _strip_for_tts(text)
    if not clean:
        return None

    if _engine == "omnivoice":
        result = _synthesize_omnivoice_single(clean, emotion, config.TTS_LANGUAGE)
        if result is not None:
            return result
        if _kokoro_ready:
            return _synthesize_kokoro(clean, None, 1.0)
        return None
    elif _engine == "cosyvoice":
        result = _synthesize_cosyvoice_single(clean, emotion, config.TTS_LANGUAGE)
        if result is not None:
            return result
        if _kokoro_ready:
            return _synthesize_kokoro(clean, None, 1.0)
        return None
    else:
        return _synthesize_kokoro(clean, None, 1.0)


def _synthesize_via_http_single(api_url: str, timeout: int, engine_name: str,
                                 text: str, emotion: str, language: str) -> Optional[str]:
    """Single-chunk HTTP synth shared by OmniVoice and CosyVoice 3.

    Both servers expose the same contract: POST /synthesize with
    {text, emotion, language} -> {audio_url}, then GET {audio_url} for the WAV.
    """
    import httpx
    try:
        resp = httpx.post(
            f"{api_url}/synthesize",
            json={"text": text, "emotion": emotion.upper(), "language": language},
            timeout=timeout,
        )
        if resp.status_code != 200:
            logger.warning(f"[tts] {engine_name} single error {resp.status_code}: {resp.text[:100]}")
            return None

        data = resp.json()
        audio_url = data.get("audio_url")
        if not audio_url:
            return None

        wav_resp = httpx.get(f"{api_url}{audio_url}", timeout=10)
        if wav_resp.status_code != 200 or len(wav_resp.content) < 1000:
            return None

        import uuid as _uuid
        filename = f"{_uuid.uuid4().hex[:12]}.wav"
        filepath = Path("audio") / filename
        filepath.parent.mkdir(exist_ok=True)
        filepath.write_bytes(wav_resp.content)

        logger.info(f"[tts] {engine_name} single: {len(wav_resp.content)} bytes ({data.get('duration', '?')}s)")
        return f"/audio/{filename}"

    except Exception as e:
        logger.warning(f"[tts] {engine_name} single failed: {e}")
        return None


def _synthesize_omnivoice_single(text: str, emotion: str = "neutral", language: str = "en") -> Optional[str]:
    import config
    return _synthesize_via_http_single(
        config.OMNIVOICE_API_URL, config.OMNIVOICE_TIMEOUT, "OmniVoice",
        text, emotion, language,
    )


def _synthesize_cosyvoice_single(text: str, emotion: str = "neutral", language: str = "en") -> Optional[str]:
    import config
    return _synthesize_via_http_single(
        config.COSYVOICE_API_URL, config.COSYVOICE_TIMEOUT, "CosyVoice",
        text, emotion, language,
    )


def synthesize(text: str, voice: Optional[str] = None, speed: float = 1.0, emotion: str = "neutral") -> Optional[str]:
    """
    Synthesize text to a WAV file.
    Returns the path to the generated audio file (relative to server root),
    or None on failure.
    """
    import config
    if not _tts_ready:
        logger.warning("[tts] TTS not ready, skipping synthesis")
        return None

    emotion = _resolve_neutral_emotion(emotion)

    if _engine == "omnivoice":
        result = _synthesize_omnivoice(text, emotion, config.TTS_LANGUAGE)
        if result is not None:
            return result
        if _kokoro_ready:
            logger.warning("[tts] OmniVoice failed (PC offline?), falling back to Kokoro")
            return _synthesize_kokoro(text, voice, speed)
        return None
    elif _engine == "cosyvoice":
        result = _synthesize_cosyvoice(text, emotion, config.TTS_LANGUAGE)
        if result is not None:
            return result
        if _kokoro_ready:
            logger.warning("[tts] CosyVoice failed (PC offline?), falling back to Kokoro")
            return _synthesize_kokoro(text, voice, speed)
        return None
    elif _engine == "qwen3tts":
        result = _synthesize_qwen3tts(text, emotion)
        if result is not None:
            return result
        if _kokoro_ready:
            logger.warning("[tts] Qwen3-TTS failed (PC offline?), falling back to Kokoro")
            return _synthesize_kokoro(text, voice, speed)
        return None
    elif _engine == "hfspace":
        result = _synthesize_hf_space(text, emotion)
        if result is not None:
            return result
        if _kokoro_ready:
            logger.warning("[tts] HF Space failed, falling back to Kokoro")
            return _synthesize_kokoro(text, voice, speed)
        return None
    else:
        return _synthesize_kokoro(text, voice, speed)


def _truncate_for_tts(text: str, max_chars: int = 300) -> str:
    """Truncate text to a TTS-friendly length, breaking at sentence boundaries."""
    if len(text) <= max_chars:
        return text
    # Try to break at the last sentence-ending punctuation within the limit
    truncated = text[:max_chars]
    last_break = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    if last_break > 0:
        return truncated[:last_break + 1]
    return truncated


def _synthesize_omnivoice(text: str, emotion: str = "neutral", language: str = "en") -> Optional[str]:
    """Synthesize with OmniVoice via FastAPI server on PC.

    Mirrors the CosyVoice 3 pipeline: single-chunk POST to /synthesize,
    OmniVoice server handles the synth in-process. Avatar-server's
    sentence-chunked streaming path uses _synthesize_omnivoice_single per chunk.
    """
    import config
    import httpx

    clean = _strip_for_tts(text)
    if not clean:
        return None
    try:
        resp = httpx.post(
            f"{config.OMNIVOICE_API_URL}/synthesize",
            json={"text": clean, "emotion": emotion.upper(), "language": language},
            timeout=config.OMNIVOICE_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(f"[tts] OmniVoice API error {resp.status_code}: {resp.text[:100]}")
            return None

        data = resp.json()
        audio_url = data.get("audio_url")
        if not audio_url:
            return None

        wav_resp = httpx.get(f"{config.OMNIVOICE_API_URL}{audio_url}", timeout=15)
        if wav_resp.status_code != 200 or len(wav_resp.content) < 1000:
            return None

        filename = f"{uuid.uuid4().hex}.wav"
        filepath = Path("audio") / filename
        filepath.parent.mkdir(exist_ok=True)
        filepath.write_bytes(wav_resp.content)
        _cleanup_audio_dir()
        logger.info(f"[tts] OmniVoice generated: {filename} ({data.get('duration', '?')}s, RTF={data.get('rtf', '?')})")
        return f"/audio/{filename}"
    except httpx.TimeoutException:
        logger.error(f"[tts] OmniVoice timeout after {config.OMNIVOICE_TIMEOUT}s")
        return None
    except httpx.ConnectError:
        logger.error(f"[tts] OmniVoice API not reachable at {config.OMNIVOICE_API_URL}")
        return None
    except Exception as e:
        logger.error(f"[tts] OmniVoice error: {e}")
        return None


def _synthesize_cosyvoice(text: str, emotion: str = "neutral", language: str = "en") -> Optional[str]:
    """Synthesize with CosyVoice 3 via FastAPI server on PC.

    Supports sentence chunking: splits text at sentence boundaries,
    synthesizes each chunk, concatenates into one WAV.
    """
    import config
    import re
    import wave
    import struct

    clean = _strip_for_tts(text)
    if not clean:
        return None

    # Send as single chunk — CosyVoice handles up to 300 chars
    # Sentence chunking produced tiny fragments that caused errors
    chunks = [clean]

    all_audio_bytes = []
    sample_rate = None

    for i, chunk in enumerate(chunks):
        try:
            import httpx
            resp = httpx.post(
                f"{config.COSYVOICE_API_URL}/synthesize",
                json={"text": chunk, "emotion": emotion.upper(), "language": language},
                timeout=config.COSYVOICE_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning(f"[tts] CosyVoice API error {resp.status_code}: {resp.text[:100]}")
                continue

            data = resp.json()
            audio_url = data.get("audio_url")
            if not audio_url:
                continue

            # Download the WAV from CosyVoice server
            wav_resp = httpx.get(
                f"{config.COSYVOICE_API_URL}{audio_url}",
                timeout=10,
            )
            if wav_resp.status_code == 200 and len(wav_resp.content) > 1000:
                all_audio_bytes.append(wav_resp.content)
                logger.info(f"[tts] CosyVoice chunk {i+1}/{len(chunks)}: {len(wav_resp.content)} bytes ({data.get('duration', '?')}s)")
            else:
                logger.warning(f"[tts] CosyVoice WAV download failed for chunk {i+1}")
        except httpx.TimeoutException:
            logger.error(f"[tts] CosyVoice timeout after {config.COSYVOICE_TIMEOUT}s on chunk {i+1}")
            break
        except httpx.ConnectError:
            logger.error(f"[tts] CosyVoice API not reachable at {config.COSYVOICE_API_URL}")
            return None
        except Exception as e:
            logger.error(f"[tts] CosyVoice error on chunk {i+1}: {e}")
            continue

    if not all_audio_bytes:
        return None

    # Concatenate WAV files
    if len(all_audio_bytes) == 1:
        combined = all_audio_bytes[0]
    else:
        combined = _concatenate_wavs(all_audio_bytes)

    if not combined:
        return None

    filename = f"{uuid.uuid4().hex}.wav"
    filepath = Path("audio") / filename
    filepath.write_bytes(combined)
    _cleanup_audio_dir()
    return f"/audio/{filename}"


def _concatenate_wavs(wav_bytes_list: list[bytes]) -> Optional[bytes]:
    """Concatenate multiple WAV files into one. Handles float32 WAVs."""
    import io
    import struct

    if not wav_bytes_list:
        return None
    if len(wav_bytes_list) == 1:
        return wav_bytes_list[0]

    # Parse WAV headers and extract raw audio data
    # WAV format: RIFF header (44 bytes typically) + raw PCM data
    all_data = b""
    header = None

    for i, wav_bytes in enumerate(wav_bytes_list):
        # Find "data" chunk in WAV
        data_pos = wav_bytes.find(b"data")
        if data_pos < 0:
            logger.warning(f"[tts] Chunk {i+1}: no data chunk found")
            continue

        # Data size is 4 bytes after "data" marker
        data_size = struct.unpack_from("<I", wav_bytes, data_pos + 4)[0]
        audio_start = data_pos + 8
        audio_data = wav_bytes[audio_start:audio_start + data_size]

        if header is None:
            header = bytearray(wav_bytes[:audio_start])

        all_data += audio_data
        logger.info(f"[tts] Concat chunk {i+1}: {len(audio_data)} audio bytes")

    if not header or not all_data:
        return wav_bytes_list[0]

    # Update header with new total size
    # RIFF chunk size = total file size - 8
    total_size = len(header) + len(all_data)
    struct.pack_into("<I", header, 4, total_size - 8)
    # data chunk size
    data_marker_pos = header.find(b"data")
    if data_marker_pos >= 0:
        struct.pack_into("<I", header, data_marker_pos + 4, len(all_data))

    logger.info(f"[tts] Concatenated {len(wav_bytes_list)} WAVs: {len(all_data)} total audio bytes")
    return bytes(header) + all_data


def _strip_for_tts(text: str) -> str:
    """Clean text for TTS — remove tags, emoji, formatting, special chars."""
    import re
    clean = text
    # Strip emotion tags
    clean = re.sub(r'\[(?:HAPPY|SAD|SURPRISED|ANGRY|THINKING|NEUTRAL)\]', '', clean)
    # Strip tool tags
    clean = re.sub(r'\[TOOL:[^\]]*\]', '', clean)
    # Strip asterisks but keep words: *sighs* → sighs
    clean = re.sub(r'\*([^*]+)\*', r'\1', clean)
    # Strip all non-speech unicode (stars, emoji, symbols)
    clean = re.sub(r'[\u2600-\u27BF\u2B50-\u2B55\uFE00-\uFE0F\U0001F300-\U0001FAFF★☆✦✧♪♫]+', '', clean)
    # Strip leading ellipsis/dots
    clean = re.sub(r'^[.\s…]+', '', clean)
    # Strip em dashes at start
    clean = re.sub(r'^[—–\-]+\s*', '', clean)
    # Replace em dashes with commas (TTS handles commas better)
    clean = clean.replace('—', ',').replace('–', ',')
    # Strip "en:" or "jp:" prefixes if they leaked through
    clean = re.sub(r'^(?:en|jp):\s*', '', clean, flags=re.IGNORECASE)
    # Strip leading stutters/sounds (nnn, mmh, tch, ugh, etc.)
    clean = re.sub(r'^(?:n+|m+h?|tch|ugh|hm+|ah+|eh+|oh+)[,.\s]+', '', clean, flags=re.IGNORECASE)
    # Strip repeated short words at start (W-wha, T-tch)
    clean = re.sub(r'^[A-Z]-[A-Za-z]+[,.\s]+\s*', '', clean)
    # Collapse whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Strip trailing punctuation junk
    clean = re.sub(r'[.\s★☆]+$', '', clean).strip()
    return _truncate_for_tts(clean)


def _synthesize_qwen3tts(text: str, emotion: str = "neutral") -> Optional[str]:
    """Synthesize with Qwen3-TTS via chichi-speech API (legacy)."""
    import config
    import requests

    # Strip emotion tags
    clean_text = re.sub(r"^\s*\[[A-Z]+\]\s*", "", text).strip()
    # Strip asterisks but keep the words inside (e.g. *sighs* -> sighs)
    clean_text = re.sub(r"\*", "", clean_text).strip()
    # Collapse multiple spaces left behind
    clean_text = re.sub(r"  +", " ", clean_text).strip()
    if not clean_text:
        return None

    original_len = len(clean_text)
    clean_text = _truncate_for_tts(clean_text)
    if len(clean_text) < original_len:
        logger.info(f"[tts] Truncated text for TTS: {original_len} -> {len(clean_text)} chars")

    tts_language = config.QWEN3TTS_LANGUAGE

    payload = {
        "text": clean_text,
        "language": tts_language,
        "emotion": emotion.lower(),
    }

    try:
        r = requests.post(
            f"{config.QWEN3TTS_API_URL}/synthesize",
            json=payload,
            timeout=config.QWEN3TTS_TIMEOUT,
        )

        if r.status_code != 200:
            error_msg = r.text[:200] if r.text else "unknown error"
            logger.error(f"[tts] Qwen3-TTS API error {r.status_code}: {error_msg}")
            return None

        if len(r.content) < 1000:
            logger.error(f"[tts] Qwen3-TTS returned too little audio data ({len(r.content)} bytes)")
            return None

        # Save the WAV response
        audio_dir = Path("audio")
        audio_dir.mkdir(exist_ok=True)
        filename = f"{uuid.uuid4().hex}.wav"
        filepath = audio_dir / filename

        with open(filepath, "wb") as f:
            f.write(r.content)

        logger.info(f"[tts] Qwen3-TTS generated: {filename} ({len(clean_text)} chars, {len(r.content)} bytes)")
        _cleanup_audio_dir()
        return f"/audio/{filename}"

    except requests.Timeout:
        logger.error(f"[tts] Qwen3-TTS timeout after {config.QWEN3TTS_TIMEOUT}s")
        return None
    except requests.ConnectionError:
        logger.error(f"[tts] Qwen3-TTS API not reachable — is chichi-speech running?")
        return None
    except Exception as e:
        logger.error(f"[tts] Qwen3-TTS synthesis failed: {e}")
        return None



def _synthesize_hf_space(text: str, emotion: str = "neutral") -> Optional[str]:
    """Synthesize with Qwen3-TTS via HuggingFace Space (ZeroGPU)."""
    import config

    if _hf_client is None:
        return None

    # Strip emotion tags and asterisk actions
    clean_text = re.sub(r"^\s*\[[A-Z]+\]\s*", "", text).strip()
    clean_text = re.sub(r"\*[^*]+\*", "", clean_text).strip()
    clean_text = re.sub(r"  +", " ", clean_text).strip()
    if not clean_text:
        return None

    original_len = len(clean_text)
    clean_text = _truncate_for_tts(clean_text)
    if len(clean_text) < original_len:
        logger.info(f"[tts] Truncated text for TTS: {original_len} -> {len(clean_text)} chars")

    try:
        result = _hf_client.predict(
            text=clean_text,
            language=config.QWEN3TTS_LANGUAGE,
            emotion=emotion.lower(),
            api_name="/synthesize",
        )

        # result is a filepath to the downloaded WAV
        if not result or not Path(result).exists():
            logger.error("[tts] HF Space returned no audio file")
            return None

        # Copy to our audio directory
        audio_dir = Path("audio")
        audio_dir.mkdir(exist_ok=True)
        filename = f"{uuid.uuid4().hex}.wav"
        filepath = audio_dir / filename

        import shutil
        shutil.copy2(result, filepath)

        file_size = filepath.stat().st_size
        logger.info(f"[tts] HF Space generated: {filename} ({len(clean_text)} chars, {file_size} bytes)")
        _cleanup_audio_dir()
        return f"/audio/{filename}"

    except Exception as e:
        logger.error(f"[tts] HF Space synthesis failed: {e}")
        return None


def _synthesize_kokoro(text: str, voice: Optional[str] = None, speed: float = 1.0) -> Optional[str]:
    """Synthesize with Kokoro-ONNX."""
    if _kokoro is None:
        return None

    import config

    # Strip emotion tags from text if present (e.g. "[HAPPY] Hello" -> "Hello")
    clean_text = re.sub(r"^\s*\[[A-Z]+\]\s*", "", text).strip()
    # Strip asterisks but keep the words inside (e.g. *sighs* -> sighs)
    clean_text = re.sub(r"\*", "", clean_text).strip()
    clean_text = re.sub(r"  +", " ", clean_text).strip()
    if not clean_text:
        return None

    voice_name = voice or config.TTS_VOICE
    synth_speed = speed if speed != 1.0 else config.TTS_SPEED

    try:
        samples, sample_rate = _kokoro.create(
            clean_text,
            voice=voice_name,
            speed=synth_speed,
            lang="en-us",
        )

        # Save to audio/ directory with a unique filename
        audio_dir = Path("audio")
        audio_dir.mkdir(exist_ok=True)
        filename = f"{uuid.uuid4().hex}.wav"
        filepath = audio_dir / filename

        # Write WAV file
        with wave.open(str(filepath), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            # Convert float32 [-1,1] to int16
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
            wf.writeframes(pcm.tobytes())

        logger.info(f"[tts] Kokoro generated: {filename} ({len(clean_text)} chars)")
        _cleanup_audio_dir()

        # Run RVC voice conversion if enabled
        if config.RVC_ENABLED:
            try:
                from services import rvc_service
                if rvc_service.is_ready():
                    rvc_result = rvc_service.convert(str(filepath))
                    if rvc_result:
                        logger.info(f"[tts] RVC converted: {filename}")
                    else:
                        logger.warning(f"[tts] RVC conversion failed, using original Kokoro voice")
            except Exception as rvc_err:
                logger.error(f"[tts] RVC error (non-fatal, using Kokoro voice): {rvc_err}")

        return f"/audio/{filename}"

    except Exception as e:
        logger.error(f"[tts] Kokoro synthesis failed: {e}")
        return None


