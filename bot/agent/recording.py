"""Whole-call recording: both sides of the conversation in one stereo WAV.

Customer on the left channel, bot on the right, time-aligned, at the call's sample rate. Saved to
bot/recordings/<call_id>/call.wav (git-ignored, stays on this machine) and served by the bot server at
/api/recordings/<call_id>.wav for the dashboard's player.
"""
from __future__ import annotations

import asyncio
import re
import wave
from pathlib import Path

from loguru import logger
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from .stt_whisper import RECORDINGS

CALL_FILE = "call.wav"
_SAFE_ID = re.compile(r"^[\w-]{1,80}$")


def call_wav(call_id: str) -> Path | None:
    """Where a call's recording lives; None for an id that could escape the recordings folder."""
    return RECORDINGS / call_id / CALL_FILE if _SAFE_ID.match(call_id or "") else None


def _write_wav(path: Path, audio: bytes, sample_rate: int, channels: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)  # 16-bit PCM, as Pipecat carries it
        w.setframerate(sample_rate)
        w.writeframes(audio)


class CallRecorder:
    """Wraps Pipecat's AudioBufferProcessor. Put `processor` after transport.output() so it sees both
    the customer's input audio and the bot's played-out audio; call `stop()` before saving the record."""

    def __init__(self, call_id: str, sample_rate: int):
        self.path = call_wav(call_id)
        self.seconds = 0.0
        self.processor = AudioBufferProcessor(sample_rate=sample_rate, num_channels=2,
                                              auto_start_recording=True)

        @self.processor.event_handler("on_audio_data")
        async def _save(_proc, audio: bytes, rate: int, channels: int):
            if not (self.path and audio):
                return
            try:
                await asyncio.to_thread(_write_wav, self.path, audio, rate, channels)
                self.seconds = round(len(audio) / (2 * channels * rate), 1)
                logger.info(f"call recording saved: {self.path} ({self.seconds}s)")
            except Exception as e:  # noqa: BLE001 - a failed save must never break the call
                logger.warning(f"could not save call recording: {e}")

    async def stop(self) -> None:
        """Flush and write the WAV now (no-op if already stopped by the pipeline ending)."""
        try:
            await self.processor.stop_recording()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"could not stop call recording: {e}")

    def saved(self) -> bool:
        return bool(self.path and self.path.exists() and self.seconds)
