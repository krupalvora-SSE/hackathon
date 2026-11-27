"""The open-source local voice (Piper), in Hindi and English, optionally re-voiced as a team member.

Piper's Hindi voices read English with a flat Hindi accent, so English sentences (the customer
switched to English, devanagari.is_english) go to a male English Piper voice instead.

"Piper + my voice": after Piper speaks each sentence, the local converter (bot/clone_tts, OpenVoice
V2, port 7861) changes its voice to the sample recorded on the /voice page (bot/local_voice.wav), so
both languages sound like the same person. Nothing leaves this machine. If the converter is down or
has no sample yet, the call carries on in plain Piper.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame
from pipecat.services.piper.tts import _SYNTHESIS_FIELDS, PiperTTSService, SynthesisConfig
from pipecat.utils.tracing.service_decorators import traced_tts
from pipecat.utils.types import assert_given

BOT_DIR = Path(__file__).resolve().parent.parent
LOCAL_VOICE_WAV = BOT_DIR / "local_voice.wav"
LOCAL_VOICE_META = BOT_DIR / "local_voice.json"
CONVERTER_URL = os.getenv("CLONE_TTS_URL", "http://127.0.0.1:7861")

# Spoken by Piper once per speaker so the converter learns Piper's own voice (what to change from).
CALIBRATION_EN = [
    "Hello, I'm calling from SolarSquare about your solar panel cleaning.",
    "Would tomorrow morning between nine and twelve work for you?",
    "Alright, I have confirmed your booking. Our team will reach on time.",
    "No problem, please tell me which day and time suits you best.",
    "Thank you, have a nice day.",
]
CALIBRATION = [
    "नमस्ते जी, मैं सोलरस्क्वेयर से बोल रहा हूँ।",
    "आपके सोलर प्लांट की सफ़ाई के लिए कल सुबह दस बजे का समय ठीक रहेगा क्या?",
    "ठीक है, मैंने आपकी बुकिंग कन्फ़र्म कर दी है। हमारी टीम समय पर पहुँच जाएगी।",
    "कोई बात नहीं, आप बताइए आपके लिए कौन सा दिन और समय सही रहेगा।",
    "धन्यवाद जी, आपका दिन शुभ हो।",
]
_calibrated: set[str] = set()  # Piper speakers the converter already knows, this process


def local_voice_name() -> str | None:
    """Name the bot introduces itself with on this engine ("Krupal - AMC bot" -> "Krupal")."""
    if not LOCAL_VOICE_WAV.exists():
        return None
    try:
        name = json.loads(LOCAL_VOICE_META.read_text()).get("name") or ""
    except (OSError, ValueError):
        return None
    return name.split(" - ")[0].strip() or None


def set_local_voice(wav: bytes | None, name: str | None = None) -> None:
    if wav is None:
        LOCAL_VOICE_WAV.unlink(missing_ok=True)
        LOCAL_VOICE_META.unlink(missing_ok=True)
        return
    LOCAL_VOICE_WAV.write_bytes(wav)
    LOCAL_VOICE_META.write_text(json.dumps({"name": name}))


async def converter_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(f"{CONVERTER_URL}/status")
            r.raise_for_status()
            return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ready": False, "error": f"converter not reachable at {CONVERTER_URL}: {e}"}


async def _ensure_source(client: httpx.AsyncClient, src: str, synth, sr: int) -> None:
    """Teach the converter Piper speaker `src` once (it caches it on disk across restarts)."""
    if src in _calibrated:
        return
    if not (await client.get(f"/source/{src}")).json().get("ready"):
        lines = CALIBRATION_EN if src.startswith("en") else CALIBRATION
        pcm = await asyncio.to_thread(lambda: b"".join(synth(t) for t in lines))
        (await client.post(f"/source/{src}", content=pcm, params={"sr": sr})).raise_for_status()
        logger.info(f"local voice: calibrated Piper speaker {src}")
    _calibrated.add(src)


async def convert(client: httpx.AsyncClient, pcm: bytes, sr: int, *, src: str, synth, tau: float) -> tuple[bytes, int]:
    """Piper PCM16 -> the local sample's voice. Raises if the converter is down or has no sample."""
    await _ensure_source(client, src, synth, sr)
    r = await client.post("/convert", content=pcm, params={"src": src, "sr": sr, "tau": tau})
    if r.status_code != 200:
        raise RuntimeError(f"converter {r.status_code}: {r.text[:200]}")
    return r.content, int(r.headers["X-Sample-Rate"])


def _client(timeout: float = 20) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=CONVERTER_URL, timeout=httpx.Timeout(timeout, connect=1))


def _synth_fn(voice):
    return lambda text, cfg=None: b"".join(c.audio_int16_bytes for c in voice.synthesize(text, syn_config=cfg))


def load_piper_voice(name: str):
    """Load a Piper voice from pipecat's cache, downloading it (~60 MB) the first time."""
    from piper import PiperVoice
    from pipecat.services.piper.tts import PIPER_CACHE_DIR

    path = PIPER_CACHE_DIR / f"{name}.onnx"
    if not path.exists():
        from piper.download_voices import download_voice

        PIPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        download_voice(name, PIPER_CACHE_DIR)
    return PiperVoice.load(path)


async def preview_wav(text: str, piper_voice: str, tau: float, en_voice: str | None = None) -> bytes:
    """One line in "Piper + my voice", as a WAV, for the /voice page."""
    import io
    import wave

    from .devanagari import is_english, to_devanagari

    if en_voice and is_english(text):
        piper_voice = en_voice
    else:
        text = to_devanagari(text)
    voice = await asyncio.to_thread(load_piper_voice, piper_voice)
    synth, sr = _synth_fn(voice), voice.config.sample_rate
    pcm = await asyncio.to_thread(synth, text)
    async with _client() as c:
        pcm, sr = await convert(c, pcm, sr, src=piper_voice, synth=synth, tau=tau)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


class BilingualPiperTTSService(PiperTTSService):
    """Piper TTS with a Hindi voice (settings.voice) and an English one (en_voice) picked per sentence,
    optionally re-voiced through the local converter (clone=True)."""

    def __init__(self, *, en_voice: str | None = None, clone: bool = False, tau: float = 0.3, **kwargs):
        super().__init__(**kwargs)
        self._clone, self._tau = clone, tau
        self._hi = (assert_given(self._settings.voice), self._voice)
        self._en = (en_voice, load_piper_voice(en_voice)) if en_voice else None
        self._client = _client(timeout=5) if clone else None  # live call: a hung converter -> plain Piper
        self._warned = False

    def _pick(self, text: str):
        from .devanagari import is_english

        return self._en if self._en and is_english(text) else self._hi

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        async def once(data: bytes):
            yield data

        try:
            src, voice = self._pick(text)
            synth = _synth_fn(voice)
            cfg = SynthesisConfig(**{n: assert_given(getattr(self._settings, n)) for n in _SYNTHESIS_FIELDS})
            await self.start_tts_usage_metrics(text)
            sr = voice.config.sample_rate
            pcm = await asyncio.to_thread(synth, text, cfg)
            if self._clone:
                try:
                    pcm, sr = await convert(self._client, pcm, sr, src=src, synth=synth, tau=self._tau)
                except Exception as e:  # noqa: BLE001 - converter down or no sample: speak plain Piper
                    if not self._warned:
                        logger.warning(f"local voice converter unavailable, speaking plain Piper: {e!r}")
                        self._warned = True
            async for frame in self._stream_audio_frames_from_iterator(once(pcm), in_sample_rate=sr,
                                                                       context_id=context_id):
                await self.stop_ttfb_metrics()
                yield frame
        except Exception as e:  # noqa: BLE001
            logger.error(f"{self} exception: {e}")
            yield ErrorFrame(error=f"Unknown error occurred: {e}")
        finally:
            await self.stop_ttfb_metrics()

    async def cleanup(self):
        await super().cleanup()
        if self._client:
            await self._client.aclose()


class PiperCloneTTSService(BilingualPiperTTSService):
    """Piper TTS whose audio goes through the local voice converter before it plays."""

    def __init__(self, **kwargs):
        super().__init__(clone=True, **kwargs)
