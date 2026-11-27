"""Speech-to-text + turn-detection bench: one call config over the audio corpus.

Whisper runs locally on segments cut by the same Silero VAD settings a call uses, so turn starts/ends
and latency match a real call.

Per clip it records: transcript, entity hits, normalised CER, voice-to-transcript latency,
turn starts/ends. Noise-only clips measure false starts (what makes the bot stop mid-sentence);
clips with a thinking pause measure premature turn ends (what makes the bot talk over a customer).
"""
from __future__ import annotations

import asyncio
import time

import numpy as np

from . import corpus
from .textnorm import cer, entity_score

CHUNK_MS = 50
FRAME_MS = 20  # Silero analysis frame fed by the transport


# ---------------------------------------------------------------- local Silero turn detection
def silero_events(pcm: bytes, sr: int, cfg: dict) -> list[tuple[str, float]]:
    """[('start'|'stop', seconds)] the call's Silero VAD would emit on this audio."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams, VADState

    vad = SileroVADAnalyzer(sample_rate=sr, params=VADParams(
        confidence=cfg["vad_confidence"], start_secs=cfg["vad_start_secs"],
        stop_secs=cfg["vad_stop_secs"], min_volume=cfg["vad_min_volume"]))
    vad.set_sample_rate(sr)
    step = int(sr * FRAME_MS / 1000) * 2
    events, prev = [], VADState.QUIET
    for i in range(0, len(pcm) - step + 1, step):
        st = vad._run_analyzer(pcm[i:i + step])
        t = (i + step) / 2 / sr
        if st == VADState.SPEAKING and prev in (VADState.QUIET, VADState.STARTING):
            events.append(("start", t))
        elif st == VADState.QUIET and prev in (VADState.SPEAKING, VADState.STOPPING):
            events.append(("stop", t))
        prev = st
    if prev in (VADState.SPEAKING, VADState.STOPPING):
        events.append(("stop", len(pcm) / 2 / sr))
    return events


# ---------------------------------------------------------------- local STT engines
def _local_clip(clip: dict, cfg: dict) -> dict:
    from . import audio as A

    from agent.stt_whisper import transcribe as whisper

    SAMPLE_RATE = 16000

    def transcribe(seg):
        return whisper(seg)[0]

    pcm, sr = corpus.pcm(clip)
    ev = silero_events(pcm, sr, cfg)
    x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    texts, final_times, starts, stops = [], [], [], []
    seg_start = None
    for kind, t in ev:
        if kind == "start":
            starts.append(t)
            seg_start = max(0.0, t - cfg["vad_start_secs"] - 0.2)  # pipecat keeps pre-speech audio
        elif kind == "stop" and seg_start is not None:
            stops.append(t)
            seg = A.resample(x[int(seg_start * sr):int(t * sr)], sr, SAMPLE_RATE)
            c = time.monotonic()
            txt = transcribe(seg)
            final_times.append(t + time.monotonic() - c)
            if txt:
                texts.append(txt)
            seg_start = None
    return {"text": " ".join(texts), "final_times": final_times, "starts": starts, "stops": stops, "errors": []}


# ---------------------------------------------------------------- scoring
def score_clip(clip: dict, r: dict) -> dict:
    row = {"id": clip["id"], "utt": clip["utt"], "condition": clip["condition"], "kind": clip["kind"],
           "ref": clip["ref"], "hyp": r["text"], "errors": r["errors"]}
    if clip["kind"] == "noise":
        row["false_start"] = bool(r["starts"]) or bool(r["text"])
        return row
    hits, total, misses = entity_score(r["text"], clip["expect"], clip["forbid"])
    row.update(ent_hits=hits, ent_total=total, ent_misses=misses, cer=round(cer(clip["ref"], r["text"]), 3),
               empty=not r["text"])
    end = clip["speech_end_s"]
    after = [t for t in r["final_times"] if t >= end - 0.3]
    row["latency_ms"] = int((after[-1] - end) * 1000) if after else None
    # a turn that ended inside the customer's own speech = the bot would have jumped in
    row["split"] = any(clip["speech_start_s"] + 0.3 < t < end - 0.3 for t in r["stops"])
    row["has_pause"] = clip["has_pause"]
    return row


def summarise(rows: list[dict]) -> dict:
    sp = [r for r in rows if r["kind"] == "speech" and not r["errors"]]
    nz = [r for r in rows if r["kind"] == "noise"]
    lat = sorted(r["latency_ms"] for r in sp if r.get("latency_ms") is not None)
    ent_t = sum(r["ent_total"] for r in sp)
    pause = [r for r in sp if r["has_pause"]]
    by_cond: dict[str, dict] = {}
    for c in sorted({r["condition"] for r in sp}):
        cs = [r for r in sp if r["condition"] == c]
        t = sum(r["ent_total"] for r in cs)
        by_cond[c] = {"entity_acc": round(sum(r["ent_hits"] for r in cs) / t, 3) if t else None,
                      "cer": round(float(np.mean([r["cer"] for r in cs])), 3)}
    return {
        "clips": len(rows), "errors": sum(bool(r["errors"]) for r in rows),
        "entity_acc": round(sum(r["ent_hits"] for r in sp) / ent_t, 3) if ent_t else None,
        "cer": round(float(np.mean([r["cer"] for r in sp])), 3) if sp else None,
        "empty_rate": round(float(np.mean([r["empty"] for r in sp])), 3) if sp else None,
        "latency_p50": int(np.percentile(lat, 50)) if lat else None,
        "latency_p90": int(np.percentile(lat, 90)) if lat else None,
        "false_start_rate": round(float(np.mean([r["false_start"] for r in nz])), 3) if nz else None,
        "split_rate": round(float(np.mean([r["split"] for r in pause])), 3) if pause else None,
        "by_condition": by_cond,
    }


async def run(cfg: dict, clips: list[dict], concurrency: int = 4) -> tuple[dict, list[dict]]:
    """Run one config over the clips. Returns (summary, per-clip rows)."""
    rows: list[dict] = []
    from agent.stt_whisper import status, warm_up

    st = status()
    if not st["ready"]:
        return {"skipped": st["message"]}, []
    await asyncio.to_thread(warm_up)
    for c in clips:  # one model, one GPU: clips run in turn (concurrency is kept for the CLI)
        rows.append(score_clip(c, await asyncio.to_thread(_local_clip, c, cfg)))
    return summarise(rows), rows
