"""Local STT with OpenAI Whisper large-v3-turbo: open source, no account, token or credits needed.

On Apple Silicon it runs on the GPU through MLX (~1.3 s for a 5 s Hindi line on an M4); elsewhere
through faster-whisper on the CPU (~6 s for the same line: too slow for live calls without a GPU).
It is not streaming: the local Silero VAD cuts the customer's speech into
segments and each is transcribed whole. A reply is transcribed as English when Whisper is
sure it is English (a customer may switch); everything else, including short Hindi replies it
mistakes for Japanese/Korean/Urdu, is transcribed as Hindi, so Hindi always comes back in Devanagari.

Setup (once): uv sync --extra whisper. The model (~1.6 GB) must already be in the local model cache;
the bot runs with HF_HUB_OFFLINE=1 and never downloads it.
"""
from __future__ import annotations

import asyncio
import json
import re
import os
import wave
from pathlib import Path
import importlib.util
import platform
import threading
import time
from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_stt

MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
CPU_MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"
NO_SPEECH_PROB = 0.6  # drop segments Whisper thinks are noise (stops "धन्यवाद" hallucinated from a cough)
EN_MIN_PROB = 0.6     # CPU path: transcribe as English only above this; everything else is Hindi
EN_PLAUSIBLE = 0.3    # MLX path: also try English when Whisper gives it at least this much
EN_MARGIN = 0.15      # ... and keep English only if its avg log-prob beats Hindi's by this much
# Unsure transcript: the call asks the customer again instead of acting on it (agent/bot.py).
# Calibrated on eval/corpus (224 speech clips + noise, with VOCAB_PROMPT): at -0.35 it flags 19% of wrong
# transcripts and every noise clip, and 1% (1/123) of right ones. Whisper's stock -1.0 flagged none here.
UNSURE_LOGPROB = float(os.getenv("WHISPER_UNSURE_LOGPROB", "-0.35"))
UNSURE_COMPRESSION = 2.4    # gzip ratio above this: a repetition loop ("कर दो कर दो कर दो ...")
# What Whisper writes for noise, echo or silence (measured on eval/corpus noise clips and seen on live
# calls): dropped as if nothing was said, so the bot doesn't answer a cough.
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "झाल", "धन्यवाद",
                  "सब्सक्राइब", "subscribe", "you", "bye", "the end", "हम्म"}
# Words customers say on this call, given to Whisper as prior context (its `prompt`). Short, in the
# script Whisper should answer in; it nudges "हजी" towards "हाँ जी" and "झाल" towards real words.
VOCAB_PROMPT = os.getenv("WHISPER_PROMPT", "हाँ जी, ठीक है, नहीं जी, चलेगा, कर दो। शनिवार सुबह, सोमवार शाम। "
                                           "सीढ़ी है, पानी है, light है। SolarSquare, slot, confirm.")

_warm = threading.Lock()
_warmed = False


def use_mlx() -> bool:
    return (platform.system() == "Darwin" and platform.machine() == "arm64"
            and importlib.util.find_spec("mlx_whisper") is not None)


def status() -> dict:
    """Is local Whisper usable here? Shown under the STT switch on the /voice page."""
    mlx = use_mlx()
    installed = mlx or importlib.util.find_spec("faster_whisper") is not None
    if not installed:
        msg = "Not installed: run `uv sync --extra whisper` in bot/."
    elif mlx:
        msg = "Ready: Apple GPU (MLX), about 1 s per reply. Loads from the local model cache."
    else:
        msg = "Ready on CPU only: about 6 s per reply, too slow for live calls without a GPU."
    return {"installed": installed, "ready": installed, "backend": "mlx" if mlx else "cpu", "message": msg}


def warm_up() -> None:
    """Load the model once per process so the customer's first reply isn't slow. Blocking."""
    global _warmed
    with _warm:
        if _warmed:
            return
        import numpy as np

        silence = np.zeros(16000, dtype=np.float32)
        if use_mlx():
            import mlx_whisper

            mlx_whisper.transcribe(silence, path_or_hf_repo=MLX_MODEL, language="hi")
        else:
            _cpu_model()
        _warmed = True
        logger.info(f"Whisper ready ({'MLX ' + MLX_MODEL if use_mlx() else 'CPU ' + CPU_MODEL})")


def _keep(text: str) -> str:
    """The transcript, or "" when it is only one of Whisper's noise phrases."""
    text = text.strip()
    bare = re.sub(r"[।॥.,!?;:'\"()\-]", " ", text).lower()
    return "" if " ".join(bare.split()) in HALLUCINATIONS else text


def is_unsure(r: dict) -> bool:
    """True when Whisper itself isn't confident in the transcript (see UNSURE_*)."""
    return bool(r["text"]) and (r["avg_logprob"] < UNSURE_LOGPROB or r["compression_ratio"] > UNSURE_COMPRESSION)


def transcribe(audio, mlx: bool | None = None) -> tuple[str, str]:
    """16 kHz float32 mono -> (text, "hi" | "en"). Blocking; call off the event loop."""
    r = transcribe_detailed(audio, mlx)
    return r["text"], r["lang"]


def transcribe_detailed(audio, mlx: bool | None = None, prompt: str | None = VOCAB_PROMPT) -> dict:
    """16 kHz float32 mono -> {text, lang, avg_logprob, compression_ratio, no_speech_prob}. Blocking."""
    mlx = use_mlx() if mlx is None else mlx
    if mlx:
        return _transcribe_mlx(audio, prompt)

    def run(language):
        segs, info = _cpu_model().transcribe(audio, language=language, beam_size=1, condition_on_previous_text=False,
                                             initial_prompt=prompt if language == "hi" else None)
        return list(segs), info

    _, info = _cpu_model().transcribe(audio, language=None, beam_size=1, condition_on_previous_text=False)
    lang = "en" if info.language == "en" and info.language_probability >= EN_MIN_PROB else "hi"
    segs, _ = run(lang)
    kept = [s for s in segs if s.no_speech_prob < NO_SPEECH_PROB]
    return {"text": _keep("".join(s.text for s in kept)), "lang": lang,
            "avg_logprob": min((s.avg_logprob for s in kept), default=0.0),
            "compression_ratio": max((s.compression_ratio for s in kept), default=0.0),
            "no_speech_prob": max((s.no_speech_prob for s in segs), default=1.0)}


def _transcribe_mlx(audio, prompt: str | None = None) -> dict:
    """One encoder pass shared by language detection and decoding (mlx_whisper.transcribe with no
    language encodes twice: ~1.9 s instead of ~1.1 s a reply). Replies are under Whisper's 30 s window."""
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.decoding import DecodingOptions, decode, detect_language
    from mlx_whisper.transcribe import ModelHolder

    model = ModelHolder.get_model(MLX_MODEL, mx.float16)
    mel = pad_or_trim(log_mel_spectrogram(audio, n_mels=model.dims.n_mels)[:N_FRAMES], N_FRAMES, axis=-2)
    features = model.encoder(mel[None].astype(mx.float16))
    _, probs = detect_language(model, features)
    max_tokens = min(224, int(len(audio) / 16000 * 20) + 20)  # a repetition loop can't run for a second

    def run(language):
        r = decode(model, features, DecodingOptions(language=language, temperature=0.0, without_timestamps=True,
                                                    fp16=True, sample_len=max_tokens,
                                                    prompt=prompt if language == "hi" and prompt else None))
        return r[0] if isinstance(r, list) else r

    # Language detection alone is unreliable on short replies: "हाँ जी" / "ठीक है" are taken for
    # Japanese or Korean, and an accented Hindi sentence can score en=0.67 (and forced to English,
    # Whisper *translates* it). So decode Hindi always, English only when it is plausible, and keep
    # English only if it is clearly more confident or the Hindi attempt is a repetition loop.
    hi = run("hi")
    best, lang = hi, "hi"
    if probs[0].get("en", 0) >= EN_PLAUSIBLE:
        en = run("en")
        if en.avg_logprob > hi.avg_logprob + EN_MARGIN or (hi.compression_ratio > 2.4 >= en.compression_ratio):
            best, lang = en, "en"
    return {"text": _keep(best.text) if best.no_speech_prob < NO_SPEECH_PROB else "", "lang": lang,
            "avg_logprob": float(best.avg_logprob), "compression_ratio": float(best.compression_ratio),
            "no_speech_prob": float(best.no_speech_prob)}


_cpu = None


def _cpu_model():
    global _cpu
    if _cpu is None:
        from faster_whisper import WhisperModel

        _cpu = WhisperModel(CPU_MODEL, device="cpu", compute_type="int8")
    return _cpu


RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"  # git-ignored, stays on this machine


class WhisperHiEnSTTService(SegmentedSTTService):
    """pipecat STT service: local Whisper, Hindi or English per reply. Needs VAD in the pipeline.

    turn_unsure: True once any segment of the customer's current turn was an unsure transcript;
    agent/bot.py resets it when a turn starts and asks again instead of acting on such a turn.
    With record=True every segment is saved as recordings/<call_id>/NNN.wav, with what Whisper
    made of it in segments.jsonl, to diagnose mishearing on real calls.
    """

    def __init__(self, *, call_id: str | None = None, record: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.turn_unsure = False
        self.turn_heard: list[dict] = []
        self._dir = RECORDINGS / call_id if (record and call_id) else None
        self._n = 0

    def start_turn(self) -> None:
        self.turn_unsure = False
        self.turn_heard = []

    @property
    def wants_wav_segments(self) -> bool:
        return False  # raw 16-bit PCM

    @property
    def segment_sample_rate(self) -> int:
        return 16000

    def can_generate_metrics(self) -> bool:
        return True

    @traced_stt
    async def _handle_transcription(self, transcript: str, is_final: bool, language: Language | None = None):
        """Tracing hook for a final transcript (pipecat services each declare their own)."""

    def _record(self, audio: bytes, r: dict, unsure: bool, secs: float) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._n += 1
            name = f"{self._n:03d}.wav"
            with wave.open(str(self._dir / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(audio)
            with open(self._dir / "segments.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"file": name, "audio_s": round(len(audio) / 32000, 2), "stt_s": round(secs, 2),
                                    "unsure": unsure, **{k: (round(v, 3) if isinstance(v, float) else v)
                                                         for k, v in r.items()}}, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"could not record STT segment: {e}")

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        import numpy as np

        await self.start_processing_metrics()
        t = time.monotonic()
        try:
            x = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
            r = await asyncio.to_thread(transcribe_detailed, x)
        except Exception as e:  # noqa: BLE001
            logger.exception("Whisper transcription failed")
            yield ErrorFrame(f"Whisper: {e}")
            return
        finally:
            await self.stop_processing_metrics()
        text, lang, unsure = r["text"], r["lang"], is_unsure(r)
        secs = time.monotonic() - t
        if self._dir:
            self._record(audio, r, unsure, secs)
        logger.debug(f"Whisper {len(audio) / 32000:.1f}s audio -> {secs:.2f}s [{lang}] "
                     f"lp={r['avg_logprob']:.2f} cr={r['compression_ratio']:.2f}{' UNSURE' if unsure else ''}: [{text}]")
        if text:
            self.turn_unsure = self.turn_unsure or unsure
            self.turn_heard.append({"text": text, "unsure": unsure})
            language = Language.EN_IN if lang == "en" else Language.HI_IN
            await self._handle_transcription(text, True, language)
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), language)


def make_whisper_stt(call_id: str | None = None, record: bool = False):
    return WhisperHiEnSTTService(call_id=call_id, record=record)
