"""Local STT with AI4Bharat IndicConformer 600M (open source, MIT, runs on CPU via ONNX).

The model is not streaming: the local Silero VAD cuts the customer's speech into segments and
each segment is transcribed whole (pipecat's SegmentedSTTService). Hindi comes back in Devanagari.

Setup (once):
  1. Accept the model terms at https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual
  2. Put a Hugging Face read token in bot/.env as HF_TOKEN=...
  3. uv sync --extra indic

Try it on a file:  uv run python -m agent.stt_indic some.wav [hi] [ctc|rnnt]
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

MODEL_ID = "ai4bharat/indic-conformer-600m-multilingual"
SAMPLE_RATE = 16000
LANGS = {"hi": Language.HI_IN, "mr": Language.MR_IN}

_model = None
_lock = threading.Lock()


def status() -> dict:
    """Is IndicConformer usable here? Shown under the STT switch on the /voice page."""
    import importlib.util

    missing = [m for m in ("torch", "transformers", "onnxruntime") if importlib.util.find_spec(m) is None]
    token = bool(os.getenv("HF_TOKEN")) or (Path.home() / ".cache/huggingface/token").exists()
    if missing:
        msg = f"Not installed ({', '.join(missing)} missing): run `uv sync --extra indic` in bot/."
    elif not token:
        msg = "No Hugging Face token: accept the model terms and add HF_TOKEN to bot/.env."
    elif _model is None:
        msg = "Ready to load (first load downloads ~2.4 GB)."
    else:
        msg = "Loaded and ready."
    return {"installed": not missing, "token": token, "loaded": _model is not None,
            "ready": not missing and token, "message": msg}


def load_model():
    """Load once per process (about 2.4 GB, downloaded to the Hugging Face cache on first use)."""
    global _model
    with _lock:
        if _model is None:
            from transformers import AutoModel

            t = time.monotonic()
            logger.info(f"loading {MODEL_ID} …")
            _model = AutoModel.from_pretrained(MODEL_ID, trust_remote_code=True,
                                               token=os.getenv("HF_TOKEN") or None)
            logger.info(f"{MODEL_ID} ready in {time.monotonic() - t:.1f}s")
    return _model


def transcribe(pcm16: bytes | np.ndarray, lang: str = "hi", decoding: str = "ctc") -> str:
    """16 kHz mono audio (int16 bytes or float array) -> text. Blocking; call off the event loop."""
    import torch

    audio = (np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
             if isinstance(pcm16, (bytes, bytearray)) else np.asarray(pcm16, dtype=np.float32))
    wav = torch.from_numpy(audio).unsqueeze(0)
    out = load_model()(wav, lang, decoding)
    return (out[0] if isinstance(out, (list, tuple)) else str(out)).strip()


class IndicConformerSTTService(SegmentedSTTService):
    """pipecat STT service around IndicConformer. Needs VAD in the pipeline (local turn detection)."""

    def __init__(self, *, lang: str = "hi", decoding: str = "ctc", **kwargs):
        super().__init__(**kwargs)
        self._lang = lang
        self._decoding = decoding

    @property
    def wants_wav_segments(self) -> bool:
        return False  # raw 16-bit PCM

    @property
    def segment_sample_rate(self) -> int:
        return SAMPLE_RATE

    def can_generate_metrics(self) -> bool:
        return True

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        await self.start_processing_metrics()
        t = time.monotonic()
        try:
            text = await asyncio.to_thread(transcribe, audio, self._lang, self._decoding)
        except Exception as e:  # noqa: BLE001
            logger.exception("IndicConformer transcription failed")
            yield ErrorFrame(f"IndicConformer: {e}")
            return
        finally:
            await self.stop_processing_metrics()
        secs = len(audio) / 2 / SAMPLE_RATE
        logger.debug(f"IndicConformer {secs:.1f}s audio -> {time.monotonic() - t:.2f}s: [{text}]")
        if text:
            language = LANGS.get(self._lang, Language.HI_IN)
            await self._handle_transcription(text, True, language)
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), language)


if __name__ == "__main__":  # quick manual test on a WAV file
    import sys
    import wave

    from dotenv import load_dotenv

    load_dotenv()
    path = sys.argv[1]
    lang = sys.argv[2] if len(sys.argv) > 2 else "hi"
    decodings = [sys.argv[3]] if len(sys.argv) > 3 else ["ctc", "rnnt"]
    with wave.open(path) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    if sr != SAMPLE_RATE:
        import torch
        import torchaudio

        x = torchaudio.functional.resample(torch.from_numpy(x), sr, SAMPLE_RATE).numpy()
    load_model()
    for d in decodings:
        t = time.monotonic()
        txt = transcribe(x, lang, d)
        print(f"{d:4s} {time.monotonic() - t:.2f}s for {len(x) / SAMPLE_RATE:.1f}s audio: {txt}")
