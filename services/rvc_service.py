"""RVC Service — character voice conversion via RVC v2.

Converts Kokoro TTS output into the character's voice using an
RVC v2 model (path configured via RVC_MODEL_PATH). Disabled by default.

Pipeline: Kokoro TTS WAV -> RVC voice conversion -> character WAV
"""

import logging
import tempfile
import time
import wave
import numpy as np
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_rvc = None
_rvc_ready = False

RVC_INFERENCE_TIMEOUT = 30

# Dedicated thread pool for RVC inference (size=1 to serialize GPU access)
_rvc_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rvc")


def init_rvc() -> bool:
    """Load the RVC model and run a warmup inference. Called once at startup."""
    global _rvc, _rvc_ready

    import config

    model_path = Path(config.RVC_MODEL_PATH)
    index_path = Path(config.RVC_INDEX_PATH)

    if not model_path.exists():
        logger.error(f"[rvc] Model not found: {model_path}")
        return False

    if not index_path.exists():
        logger.warning(f"[rvc] Index not found: {index_path} — will run without index (lower quality)")
        index_str = ""
    else:
        index_str = str(index_path)

    try:
        from rvc_python.infer import RVCInference

        _rvc = RVCInference(device=config.RVC_DEVICE)
        _rvc.load_model(str(model_path), version="v2", index_path=index_str)
        _rvc.set_params(
            f0method=config.RVC_F0_METHOD,
            f0up_key=config.RVC_PITCH_SHIFT,
            index_rate=config.RVC_INDEX_RATE,
            filter_radius=config.RVC_FILTER_RADIUS,
            rms_mix_rate=config.RVC_RMS_MIX_RATE,
            protect=config.RVC_PROTECT,
            resample_sr=config.RVC_RESAMPLE_SR,
        )

        logger.info("[rvc] Running warmup inference...")
        _warmup()

        _rvc_ready = True
        logger.info(f"[rvc] RVC ready — model={model_path.name}, device={config.RVC_DEVICE}")
        return True

    except Exception as e:
        logger.error(f"[rvc] Failed to load RVC: {e}")
        _rvc_ready = False
        return False


def _warmup():
    """Run a short dummy inference to warm up CUDA kernels."""
    try:
        warmup_in = Path(tempfile.gettempdir()) / "rvc_warmup_in.wav"
        warmup_out = Path(tempfile.gettempdir()) / "rvc_warmup_out.wav"

        with wave.open(str(warmup_in), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            samples = (np.random.randn(24000) * 100).astype(np.int16)
            wf.writeframes(samples.tobytes())

        _rvc.infer_file(str(warmup_in), str(warmup_out))

        warmup_in.unlink(missing_ok=True)
        warmup_out.unlink(missing_ok=True)
        logger.info("[rvc] Warmup done")
    except Exception as e:
        logger.warning(f"[rvc] Warmup failed (non-fatal): {e}")


def is_ready() -> bool:
    return _rvc_ready


def _run_inference(input_path: str, output_path: str):
    """Run RVC inference — submitted to the dedicated thread pool by convert()."""
    _rvc.infer_file(input_path, output_path)


def _resample_wav(wav_path: Path, target_sr: int):
    """Resample a WAV file in-place to the target sample rate.

    rvc_python has a bug where resample_sr resamples the audio data but
    writes the WAV header with the original model sample rate (40kHz).
    So we do our own clean resample here using scipy.
    """
    from scipy.io import wavfile
    from scipy.signal import resample_poly
    from math import gcd

    sr, data = wavfile.read(str(wav_path))
    if sr == target_sr:
        return

    g = gcd(target_sr, sr)
    up = target_sr // g
    down = sr // g

    resampled = resample_poly(data, up, down).astype(data.dtype)

    wavfile.write(str(wav_path), target_sr, resampled)
    logger.debug(f"[rvc] Resampled {wav_path.name}: {sr}Hz -> {target_sr}Hz")


def convert(input_wav: str) -> Optional[str]:
    """Convert a WAV file through RVC voice conversion.

    Args:
        input_wav: Path to the input WAV file (from Kokoro TTS).

    Returns:
        Path to the converted WAV file, or None on failure.
        On failure the original Kokoro WAV is preserved intact.
    """
    if not _rvc_ready or _rvc is None:
        logger.warning("[rvc] RVC not ready, skipping voice conversion")
        return None

    input_path = Path(input_wav)
    if not input_path.exists():
        logger.error(f"[rvc] Input file not found: {input_wav}")
        return None

    output_path = input_path.with_suffix(".rvc.wav")

    try:
        t0 = time.perf_counter()

        future = _rvc_executor.submit(_run_inference, str(input_path), str(output_path))
        future.result(timeout=RVC_INFERENCE_TIMEOUT)

        elapsed = time.perf_counter() - t0

        if not output_path.exists() or output_path.stat().st_size == 0:
            logger.error("[rvc] Output file missing or empty after inference")
            if output_path.exists():
                output_path.unlink()
            return None

        import config
        if config.RVC_OUTPUT_SR > 0:
            _resample_wav(output_path, config.RVC_OUTPUT_SR)

        input_path.unlink()
        output_path.rename(input_path)

        logger.info(f"[rvc] Converted: {input_path.name} ({elapsed:.2f}s)")
        return str(input_path)

    except FuturesTimeoutError:
        logger.error(f"[rvc] Inference timed out after {RVC_INFERENCE_TIMEOUT}s — skipping RVC")
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return None

    except Exception as e:
        logger.error(f"[rvc] Conversion failed: {e}")
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return None
