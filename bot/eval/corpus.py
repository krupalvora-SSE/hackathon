"""Build the audio test corpus: synthetic customer lines under phone conditions, noise-only clips,
and any real recordings dropped into eval/corpus/real/.

  uv run python -m eval.corpus                    # build/refresh (TTS results are cached)
  uv run python -m eval.corpus --speakers rahul priya ratan --paces 0.9 1.0 1.2

Real recordings: put `name.wav` plus `name.txt` (exact words said) in eval/corpus/real/, optionally
`name.json` with {"expect": [...], "forbid": [...]}. They stay local (eval/corpus/ is git-ignored),
which keeps customer audio inside the company.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path

import numpy as np
import yaml
from dotenv import load_dotenv

from . import audio as A

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
CACHE = CORPUS / "_tts_cache"
MANIFEST = CORPUS / "manifest.json"
UTTERANCES = ROOT / "utterances.yaml"
SR = 16000
PAUSE_S = 1.3          # thinking pause inside a sentence: must NOT end the customer's turn
LEAD_S, TAIL_S = 0.6, 1.8

CONDITIONS = {
    # name: (phone line?, noise kind, SNR dB, dropouts)
    "clean": (False, None, None, False),
    "phone": (True, None, None, False),
    "phone_noisy": (True, "pink", 10, False),
    "phone_babble": (True, "babble", 8, True),
}


def _tts(text: str, speaker: str, pace: float) -> np.ndarray:
    key = hashlib.sha1(f"{text}|{speaker}|{pace}".encode()).hexdigest()[:16]
    path = CACHE / f"{key}.wav"
    if not path.exists():
        from sarvamai import SarvamAI

        client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
        r = client.text_to_speech.convert(text=text, target_language_code="hi-IN", speaker=speaker,
                                          model="bulbul:v3", pace=pace, speech_sample_rate=SR)
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(r.audios[0]))
    x, sr = A.read_wav(path)
    x = A.resample(x, sr, SR)
    nz = np.flatnonzero(np.abs(x) > 0.01)  # trim TTS's own leading/trailing silence
    return x[nz[0]:nz[-1] + 1] if len(nz) else x


def _babble(pool: list[np.ndarray], n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, np.float32)
    for _ in range(4):
        s = pool[rng.integers(len(pool))]
        s = np.resize(s, n)
        out += np.roll(s, rng.integers(n))
    return out


def build(speakers: list[str], paces: list[float], conditions: list[str], seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    utts = yaml.safe_load(UTTERANCES.read_text())
    clips: list[dict] = []
    pool: list[np.ndarray] = []

    for u in utts:
        for spk in speakers:
            for pace in paces:
                parts = [p.strip() for p in u["text"].split("|")]
                segs = [_tts(p, spk, pace) for p in parts]
                gap = np.zeros(int(PAUSE_S * SR), np.float32)
                speech = segs[0]
                for s in segs[1:]:
                    speech = np.concatenate([speech, gap, s])
                pool.append(speech)
                x = A.pad(speech * 0.7, SR, LEAD_S, TAIL_S)
                for cond in conditions:
                    phone, noise, snr, drop = CONDITIONS[cond]
                    y = x
                    if noise == "pink":
                        y = A.mix_snr(y, A.pink_noise(len(y), rng), snr)
                    elif noise == "babble" and len(pool) > 3:
                        y = A.mix_snr(y, _babble(pool, len(y), rng), snr)
                    sr = SR
                    if phone:
                        y, sr = A.phone_line(y, SR, rng, dropouts=drop), 8000
                    cid = f"{u['id']}.{spk}.{pace}.{cond}"
                    path = CORPUS / "clips" / f"{cid}.wav"
                    A.write_wav(path, y, sr)
                    clips.append({
                        "id": cid, "utt": u["id"], "kind": "speech", "condition": cond, "speaker": spk,
                        "pace": pace, "path": str(path.relative_to(CORPUS)), "sr": sr,
                        "ref": " ".join(parts), "expect": u.get("expect", []), "forbid": u.get("forbid", []),
                        "has_pause": len(parts) > 1, "speech_start_s": LEAD_S,
                        "speech_end_s": round(LEAD_S + len(speech) / SR, 3),
                        "duration_s": round(len(y) / sr, 3),
                    })

    # Noise-only clips: anything heard here is a false start (would cut the bot off mid-sentence).
    for i, (name, make) in enumerate({
        "hiss": lambda n: A.pink_noise(n, rng) * 0.05,
        "babble_far": lambda n: _babble(pool, n, rng) * 0.04,
        "cough": lambda n: np.concatenate([np.zeros(n // 2, np.float32),
                                           (rng.standard_normal(int(0.15 * SR)) * 0.4).astype(np.float32),
                                           np.zeros(n - n // 2 - int(0.15 * SR), np.float32)]),
        "click_hum": lambda n: (0.03 * np.sin(2 * np.pi * 50 * np.arange(n) / SR)
                                + (np.arange(n) % (SR // 2) < 40) * 0.5).astype(np.float32),
    }.items()):
        x = make(4 * SR)
        y, sr = A.phone_line(x, SR, rng), 8000
        cid = f"noise.{name}"
        path = CORPUS / "clips" / f"{cid}.wav"
        A.write_wav(path, y, sr)
        clips.append({"id": cid, "utt": cid, "kind": "noise", "condition": "phone", "path": str(path.relative_to(CORPUS)),
                      "sr": sr, "ref": "", "expect": [], "forbid": [], "has_pause": False,
                      "speech_start_s": None, "speech_end_s": None, "duration_s": round(len(y) / sr, 3)})

    # Real recordings (kept local).
    for wav in sorted((CORPUS / "real").glob("*.wav")):
        txt = wav.with_suffix(".txt")
        if not txt.exists():
            print(f"skip {wav.name}: no {txt.name} with the words said")
            continue
        meta = json.loads(wav.with_suffix(".json").read_text()) if wav.with_suffix(".json").exists() else {}
        x, sr = A.read_wav(wav)
        nz = np.flatnonzero(np.abs(x) > 0.02)
        clips.append({"id": f"real.{wav.stem}", "utt": f"real.{wav.stem}", "kind": "speech", "condition": "real",
                      "path": str(wav.relative_to(CORPUS)), "sr": sr, "ref": txt.read_text().strip(),
                      "expect": meta.get("expect", []), "forbid": meta.get("forbid", []),
                      "has_pause": meta.get("has_pause", False),
                      "speech_start_s": round(nz[0] / sr, 3) if len(nz) else 0,
                      "speech_end_s": round(nz[-1] / sr, 3) if len(nz) else 0,
                      "duration_s": round(len(x) / sr, 3)})

    MANIFEST.write_text(json.dumps(clips, ensure_ascii=False, indent=1))
    return clips


def load(limit: int | None = None, conditions: list[str] | None = None) -> list[dict]:
    if not MANIFEST.exists():
        raise SystemExit("no corpus yet: run `uv run python -m eval.corpus` first")
    clips = json.loads(MANIFEST.read_text())
    if conditions:
        clips = [c for c in clips if c["condition"] in conditions or c["kind"] == "noise"]
    if limit:  # keep the mix: every noise clip plus an even spread of speech clips
        speech = [c for c in clips if c["kind"] == "speech"]
        step = max(1, len(speech) // limit)
        clips = speech[::step][:limit] + [c for c in clips if c["kind"] == "noise"]
    return clips


def pcm(clip: dict) -> tuple[bytes, int]:
    x, sr = A.read_wav(CORPUS / clip["path"])
    return A.to_pcm16(x), sr


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--speakers", nargs="+", default=["rahul", "priya"])
    ap.add_argument("--paces", nargs="+", type=float, default=[1.0, 1.2])
    ap.add_argument("--conditions", nargs="+", default=list(CONDITIONS), choices=list(CONDITIONS))
    a = ap.parse_args()
    clips = build(a.speakers, a.paces, a.conditions)
    n_speech = sum(c["kind"] == "speech" for c in clips)
    print(f"{len(clips)} clips ({n_speech} speech, {len(clips) - n_speech} noise-only) -> {MANIFEST}")


if __name__ == "__main__":
    main()
