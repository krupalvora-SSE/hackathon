"""Local voice-cloning converter: re-voices Piper's Hindi speech as the recorded speaker (OpenVoice V2).

Open source, runs on this machine (Apple GPU if present, else CPU). Lives in its own environment
because its torch pin clashes with the bot's. Start it with:

    cd bot/clone_tts && uv run uvicorn server:app --port 7861

The bot posts each Piper sentence as raw 16-bit mono PCM and gets the converted PCM back.
  - target voice: bot/local_voice.wav (saved from the /voice page), reloaded when it changes
  - source voice: one embedding per Piper speaker, set once from a few calibration sentences
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the converter checkpoint is already on disk: never download

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from fastapi import FastAPI, HTTPException, Request, Response  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from loguru import logger  # noqa: E402
from openvoice import utils  # noqa: E402
from openvoice.mel_processing import spectrogram_torch  # noqa: E402
from openvoice.models import SynthesizerTrn  # noqa: E402

HERE = Path(__file__).resolve().parent
TARGET_WAV = Path(os.getenv("LOCAL_VOICE_WAV", HERE.parent / "local_voice.wav"))
CACHE = HERE / "cache"
DEVICE = os.getenv("CLONE_DEVICE") or ("mps" if torch.backends.mps.is_available() else "cpu")

_ckpt = Path(snapshot_download("myshell-ai/OpenVoiceV2", allow_patterns=["converter/*"])) / "converter"
_hps = utils.get_hparams_from_file(str(_ckpt / "config.json"))
SR = _hps.data.sampling_rate
_model = SynthesizerTrn(len(getattr(_hps, "symbols", [])), _hps.data.filter_length // 2 + 1,
                        n_speakers=_hps.data.n_speakers, **_hps.model).to(DEVICE).eval()
_model.load_state_dict(torch.load(_ckpt / "checkpoint.pth", map_location=DEVICE)["model"], strict=False)
_lock = threading.Lock()  # one inference at a time on the shared model
_target: dict = {"mtime": None, "se": None}
_sources: dict[str, torch.Tensor] = {}

app = FastAPI()


def _spec(audio: np.ndarray) -> torch.Tensor:
    y = torch.from_numpy(audio).float().unsqueeze(0).to(DEVICE)
    return spectrogram_torch(y, _hps.data.filter_length, SR, _hps.data.hop_length, _hps.data.win_length,
                             center=False)


def _embed(clips: list[np.ndarray]) -> torch.Tensor:
    with _lock, torch.no_grad():
        return torch.stack([_model.ref_enc(_spec(a).transpose(1, 2)).unsqueeze(-1) for a in clips]).mean(0)


def _pcm(body: bytes, sr: int) -> np.ndarray:
    audio = np.frombuffer(body, dtype=np.int16).astype(np.float32) / 32768
    return librosa.resample(audio, orig_sr=sr, target_sr=SR) if sr != SR else audio


def _target_se() -> torch.Tensor | None:
    """Speaker embedding of bot/local_voice.wav, recomputed whenever the file changes."""
    try:
        mtime = TARGET_WAV.stat().st_mtime
    except FileNotFoundError:
        _target.update(mtime=None, se=None)
        return None
    if mtime != _target["mtime"]:
        audio, _ = librosa.load(TARGET_WAV, sr=SR)
        audio, _ = librosa.effects.trim(audio, top_db=35)
        # several ~4 s windows average out better than one long spectrogram
        win = 4 * SR
        clips = [audio[i:i + win] for i in range(0, max(len(audio) - SR, 1), win)] or [audio]
        _target.update(mtime=mtime, se=_embed([c for c in clips if len(c) > SR] or [audio]))
        logger.info(f"target voice loaded from {TARGET_WAV.name}: {len(audio) / SR:.1f}s")
    return _target["se"]


def _source_se(src: str) -> torch.Tensor | None:
    if src not in _sources and (p := CACHE / f"src_{src}.pt").exists():
        _sources[src] = torch.load(p, map_location=DEVICE)
    return _sources.get(src)


@app.get("/status")
def status():
    return {"ready": True, "device": DEVICE, "sample_rate": SR, "target": _target_se() is not None,
            "target_file": str(TARGET_WAV), "sources": sorted(p.stem[4:] for p in CACHE.glob("src_*.pt"))}


@app.get("/source/{src}")
def has_source(src: str):
    return {"src": src, "ready": _source_se(src) is not None}


@app.post("/source/{src}")
async def set_source(src: str, request: Request, sr: int):
    """Calibrate a Piper speaker: body is several sentences of its raw PCM16, joined end to end."""
    audio = _pcm(await request.body(), sr)
    win = 4 * SR
    se = _embed([audio[i:i + win] for i in range(0, len(audio) - SR, win)] or [audio])
    CACHE.mkdir(exist_ok=True)
    torch.save(se.cpu(), CACHE / f"src_{src}.pt")
    _sources[src] = se
    logger.info(f"source voice {src} calibrated from {len(audio) / SR:.1f}s")
    return {"src": src, "ready": True}


@app.post("/convert")
async def convert(request: Request, src: str, sr: int, tau: float = 0.3):
    """Body: raw PCM16 mono at `sr`. Returns raw PCM16 mono at the converter's rate (X-Sample-Rate)."""
    tgt, src_se = _target_se(), _source_se(src)
    if tgt is None:
        raise HTTPException(409, f"no target voice: record one on the /voice page ({TARGET_WAV.name})")
    if src_se is None:
        raise HTTPException(409, f"source voice {src} not calibrated")
    audio = _pcm(await request.body(), sr)
    t = time.perf_counter()
    with _lock, torch.no_grad():
        s = _spec(audio)
        out = _model.voice_conversion(s, torch.LongTensor([s.size(-1)]).to(DEVICE), sid_src=src_se,
                                      sid_tgt=tgt, tau=tau)[0][0, 0].float().cpu().numpy()
    logger.debug(f"converted {len(audio) / SR:.2f}s in {(time.perf_counter() - t) * 1000:.0f} ms")
    pcm = (np.clip(out, -1, 1) * 32767).astype(np.int16).tobytes()
    return Response(pcm, media_type="application/octet-stream", headers={"X-Sample-Rate": str(SR)})


@app.on_event("startup")
def _warm_up():
    with _lock, torch.no_grad():  # first MPS pass compiles kernels; pay it here, not on a call
        s = _spec(np.zeros(SR, dtype=np.float32))
        g = torch.zeros(1, _hps.model.gin_channels, 1, device=DEVICE)
        _model.voice_conversion(s, torch.LongTensor([s.size(-1)]).to(DEVICE), sid_src=g, sid_tgt=g, tau=0.3)
    logger.info(f"converter ready on {DEVICE}, target={'yes' if _target_se() is not None else 'none yet'}")
