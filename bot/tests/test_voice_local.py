"""Piper + my voice: engine option, intro name, and plain-Piper fallback when the converter is down."""
import asyncio

from agent import config, voice, voice_local
from agent.voice import clone_in_use


def _use_tmp_sample(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_local, "LOCAL_VOICE_WAV", tmp_path / "local_voice.wav")
    monkeypatch.setattr(voice_local, "LOCAL_VOICE_META", tmp_path / "local_voice.json")


def test_engine_option_and_sarvam_clone_off(monkeypatch):
    assert "piper_clone" in [o[0] for o in config.BY_KEY["tts_provider"]["options"]]
    monkeypatch.setattr("agent.voice.active_voice_id", lambda: "svc-x")
    assert clone_in_use({"tts_provider": "piper_clone", "tts_source": "clone"}) is None


def test_intro_name_comes_from_local_sample(monkeypatch, tmp_path):
    _use_tmp_sample(monkeypatch, tmp_path)
    monkeypatch.setattr("agent.config.load", lambda: {"tts_provider": "piper_clone"})
    assert voice.active_voice_name() is None  # no sample yet
    voice_local.set_local_voice(b"RIFF....", "Krupal - local")
    assert voice.active_voice_name() == "Krupal"
    voice_local.set_local_voice(None)
    assert not voice_local.LOCAL_VOICE_WAV.exists() and voice.active_voice_name() is None


def test_converter_down_speaks_plain_piper(monkeypatch):
    from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame
    from pipecat.services.piper.tts import PiperTTSService

    monkeypatch.setattr(voice_local, "CONVERTER_URL", "http://127.0.0.1:9")  # nothing listens here
    tts = voice_local.PiperCloneTTSService(settings=PiperTTSService.Settings(voice="hi_IN-pratham-medium"))

    async def noop(*a, **k):
        pass
    tts.start_tts_usage_metrics = tts.stop_ttfb_metrics = noop

    async def frames():
        out = [f async for f in tts.run_tts("नमस्ते जी।", "ctx")]
        await tts._client.aclose()
        return out
    out = asyncio.run(frames())
    assert any(isinstance(f, TTSAudioRawFrame) and f.audio for f in out)
    assert not any(isinstance(f, ErrorFrame) for f in out) and tts._warned


def test_fine_tuned_piper_voice_names_the_bot(monkeypatch):
    monkeypatch.setattr("agent.config.load", lambda: {"tts_provider": "piper", "piper_voice": "hi_IN-jasir-medium"})
    assert voice.active_voice_name() == "Jasir"
    monkeypatch.setattr("agent.config.load", lambda: {"tts_provider": "piper", "piper_voice": "hi_IN-pratham-medium"})
    assert voice.active_voice_name() is None  # stock speaker: persona name


def test_whisper_is_an_stt_option_and_reports_status():
    from agent import stt_whisper

    assert "whisper" in [o[0] for o in config.BY_KEY["stt_provider"]["options"]]
    st = stt_whisper.status()
    assert set(st) >= {"installed", "ready", "backend", "message"}


def test_local_stt_engines_declare_transcription_hook():
    # pipecat's STT base class has no _handle_transcription; each service declares its own.
    # Missing it made every Whisper/IndicConformer reply raise, so the bot never answered.
    from agent.stt_indic import IndicConformerSTTService
    from agent.stt_whisper import WhisperHiEnSTTService
    for cls in (WhisperHiEnSTTService, IndicConformerSTTService):
        assert "_handle_transcription" in vars(cls), cls.__name__
