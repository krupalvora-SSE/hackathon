"""IndicConformer STT wiring, with a stand-in for the real (gated, 2.4 GB) model."""
import asyncio

import numpy as np
import pytest

pytest.importorskip("torch")  # only with `uv sync --extra indic`

from pipecat.frames.frames import TranscriptionFrame  # noqa: E402

from agent import config, stt_indic  # noqa: E402


class FakeModel:
    def __init__(self):
        self.calls = []

    def __call__(self, wav, lang, decoding):
        self.calls.append((tuple(wav.shape), lang, decoding, float(wav.abs().max())))
        return "हाँ शनिवार ठीक है"


def test_transcribe_converts_pcm_and_passes_language(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(stt_indic, "_model", fake)
    pcm = (np.ones(16000, dtype=np.int16) * 16384).tobytes()  # 1 s at half scale
    assert stt_indic.transcribe(pcm, "hi", "rnnt") == "हाँ शनिवार ठीक है"
    shape, lang, decoding, peak = fake.calls[0]
    assert shape == (1, 16000) and lang == "hi" and decoding == "rnnt" and abs(peak - 0.5) < 1e-3


def test_service_yields_transcription_frame(monkeypatch):
    monkeypatch.setattr(stt_indic, "_model", FakeModel())
    svc = stt_indic.IndicConformerSTTService(lang="hi", decoding="ctc")
    assert svc.wants_wav_segments is False and svc.segment_sample_rate == 16000

    async def run():
        async def noop(*a, **k):
            return None
        svc.start_processing_metrics = svc.stop_processing_metrics = noop
        svc._handle_transcription = noop
        return [f async for f in svc.run_stt(np.zeros(8000, dtype=np.int16).tobytes())]

    frames = asyncio.run(run())
    assert len(frames) == 1 and isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "हाँ शनिवार ठीक है"


def test_config_offers_indic_engine():
    f = config.BY_KEY["stt_provider"]
    assert ["sarvam", "indic", "whisper"] == [o[0] for o in f["options"]] and f["default"] == "sarvam"


def test_status_needs_token(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(stt_indic.Path, "home", lambda: tmp_path)
    s = stt_indic.status()
    assert s["installed"] and not s["ready"] and "HF_TOKEN" in s["message"]
    monkeypatch.setenv("HF_TOKEN", "x")
    assert stt_indic.status()["ready"]
