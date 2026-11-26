"""Audio helpers for the eval corpus: WAV I/O, resampling, phone-line simulation, noise.

Everything is mono int16 PCM. Phone conditions approximate Exotel's 8 kHz G.711 leg: band-limit to
300-3400 Hz, mu-law round trip, optional packet-loss-like dropouts.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, sr


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def resample(x: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target:
        return x
    n = int(round(len(x) * target / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _bandpass(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / sr)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(x)).astype(np.float32)


def _mulaw(x: np.ndarray, mu: int = 255) -> np.ndarray:
    y = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
    y = np.round((y + 1) / 2 * mu) / mu * 2 - 1          # 8-bit quantisation
    return (np.sign(y) * ((1 + mu) ** np.abs(y) - 1) / mu).astype(np.float32)


def phone_line(x: np.ndarray, sr: int, rng: np.random.Generator, dropouts: bool = False) -> np.ndarray:
    """Return 8 kHz audio that sounds like a G.711 call leg."""
    y = resample(x, sr, 8000)
    y = _bandpass(y, 8000, 300, 3400)
    y = _mulaw(y)
    if dropouts:  # 20 ms holes, ~2% of frames
        frame = 160
        for i in range(0, len(y) - frame, frame):
            if rng.random() < 0.02:
                y[i:i + frame] = 0
    return y


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    spec /= np.sqrt(np.maximum(1, np.arange(len(spec))))
    p = np.fft.irfft(spec, n)
    return (p / (np.abs(p).max() + 1e-9)).astype(np.float32)


def mix_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    noise = np.resize(noise, len(speech))
    ps = np.mean(speech ** 2) + 1e-12
    pn = np.mean(noise ** 2) + 1e-12
    return (speech + noise * np.sqrt(ps / (pn * 10 ** (snr_db / 10)))).astype(np.float32)


def pad(x: np.ndarray, sr: int, before: float, after: float) -> np.ndarray:
    return np.concatenate([np.zeros(int(before * sr), np.float32), x, np.zeros(int(after * sr), np.float32)])
