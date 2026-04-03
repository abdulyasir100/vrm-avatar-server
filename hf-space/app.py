"""Suisei TTS — Qwen3-TTS voice clone on HuggingFace ZeroGPU.

Gradio API endpoint: synthesize(text, language, emotion) → WAV file.
Voice prompt is pre-computed at startup from suisei_ref_happy.wav.
"""

import os
import io
import time
import logging
import numpy as np
import soundfile as sf
import spaces
import gradio as gr
import torch
from pathlib import Path
from qwen_tts import Qwen3TTSModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("suisei-tts")

# Global state
model = None
VOICE_PROMPT = None

REF_AUDIO = "suisei_ref_happy.wav"
REF_TEXT = "Minna, konnichiwa! Hoshimachi Suisei desu!"
VOICE_PROMPT_PATH = Path("voice_prompt.pt")
INFERENCE_TIMEOUT = 60


def load_model():
    """Load model and voice prompt at startup."""
    global model, VOICE_PROMPT

    logger.info("Loading Qwen3-TTS model...")
    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(
        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        device_map="cuda:0",
        dtype=torch.bfloat16,
    )
    logger.info(f"Model loaded in {time.time() - t0:.1f}s")

    # Try loading pre-computed voice prompt tensor
    if VOICE_PROMPT_PATH.exists():
        logger.info("Loading cached voice prompt tensor...")
        VOICE_PROMPT = torch.load(VOICE_PROMPT_PATH, map_location="cuda:0", weights_only=True)
        logger.info("Voice prompt loaded from cache")
    elif os.path.exists(REF_AUDIO):
        logger.info(f"Creating voice prompt from {REF_AUDIO}...")
        t0 = time.time()
        VOICE_PROMPT = model.create_voice_clone_prompt(
            ref_audio=REF_AUDIO,
            x_vector_only_mode=True,
        )
        # Save for future cold starts
        torch.save(VOICE_PROMPT, VOICE_PROMPT_PATH)
        logger.info(f"Voice prompt cached in {time.time() - t0:.1f}s")
    else:
        logger.error(f"No reference audio found: {REF_AUDIO}")


@spaces.GPU(duration=60)
def synthesize(text: str, language: str = "English", emotion: str = "neutral") -> str:
    """Synthesize speech. Returns path to WAV file."""
    global model, VOICE_PROMPT

    if model is None:
        load_model()

    if VOICE_PROMPT is None:
        raise gr.Error("Voice prompt not initialized")

    if not text or not text.strip():
        raise gr.Error("Empty text")

    text = text.strip()[:300]  # cap length

    logger.info(f"Synthesize: lang={language}, text='{text[:60]}...'")
    t0 = time.time()

    wavs, sr = model.generate_voice_clone(
        text=text,
        language=language,
        voice_clone_prompt=VOICE_PROMPT,
    )

    audio_data = wavs[0]
    if hasattr(audio_data, "cpu"):
        audio_data = audio_data.cpu().float().numpy()

    elapsed = time.time() - t0
    logger.info(f"Inference done in {elapsed:.1f}s — {len(audio_data)} samples")

    # Write to temp WAV
    out_path = "/tmp/output.wav"
    sf.write(out_path, audio_data, sr, format="WAV")
    return out_path


# Build Gradio interface
demo = gr.Interface(
    fn=synthesize,
    inputs=[
        gr.Textbox(label="Text", placeholder="Enter text to synthesize..."),
        gr.Textbox(label="Language", value="English"),
        gr.Textbox(label="Emotion", value="neutral"),
    ],
    outputs=gr.Audio(label="Output", type="filepath"),
    title="Suisei TTS",
    description="Qwen3-TTS voice clone of Hoshimachi Suisei",
    api_name="synthesize",
)

if __name__ == "__main__":
    demo.launch()
