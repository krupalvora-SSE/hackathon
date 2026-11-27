"""Whole-call recording: both sides land in one stereo WAV, customer left, bot right."""
import asyncio
import wave

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.tests.utils import run_test

from agent import recording


def test_call_wav_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "RECORDINGS", tmp_path)
    assert recording.call_wav("CALL-1a2b") == tmp_path / "CALL-1a2b" / "call.wav"
    for bad in ("../etc", "a/b", "", "x" * 81):
        assert recording.call_wav(bad) is None


def test_records_both_sides(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "RECORDINGS", tmp_path)
    asyncio.run(_record_both_sides())


async def _record_both_sides():
    rec = recording.CallRecorder("CALL-test", 16000)
    chunk = 320 * 2  # 20 ms of 16 kHz 16-bit mono
    customer = b"\x10\x00" * (chunk // 2)
    bot = b"\x20\x00" * (chunk // 2)
    frames = [InputAudioRawFrame(audio=customer, sample_rate=16000, num_channels=1) for _ in range(5)]
    frames += [OutputAudioRawFrame(audio=bot, sample_rate=16000, num_channels=1) for _ in range(5)]
    await run_test(rec.processor, frames_to_send=frames, expected_down_frames=None)
    await rec.stop()
    assert rec.saved()
    with wave.open(str(rec.path)) as w:
        assert (w.getnchannels(), w.getframerate()) == (2, 16000)
        data = w.readframes(w.getnframes())
    left = {data[i:i + 2] for i in range(0, len(data), 4)}
    right = {data[i + 2:i + 4] for i in range(0, len(data), 4)}
    assert b"\x10\x00" in left and b"\x20\x00" in right
