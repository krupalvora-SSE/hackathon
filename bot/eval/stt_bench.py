"""Speech-to-text + turn-detection bench: one call config over the audio corpus.

Sarvam is driven over its realtime websocket with the exact query parameters a call sends
(agent.bot.make_sarvam_stt builds them), audio streamed at real-time pace so latency is real.
IndicConformer runs locally, segmented by the same Silero VAD settings a call would use.

Per clip it records: transcript, entity hits, normalised CER, voice-to-transcript latency,
turn starts/ends. Noise-only clips measure false starts (what makes the bot stop mid-sentence);
clips with a thinking pause measure premature turn ends (what makes the bot talk over a customer).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from urllib.parse import urlencode

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


# ---------------------------------------------------------------- Sarvam realtime
async def _sarvam_clip(clip: dict, cfg: dict) -> dict:
    import websockets

    from agent.bot import make_sarvam_stt

    pcm, sr = corpus.pcm(clip)
    svc = make_sarvam_stt(cfg, sample_rate=sr)
    svc._sample_rate = sr  # set by the pipeline at start; the bench has no pipeline
    url = f"{svc._base_url}?{urlencode(svc._query_params())}"
    manual = cfg["endpointing"] != "sarvam"
    plan = silero_events(pcm, sr, cfg) if manual else []

    events: list[tuple[str, float, str]] = []
    t0 = 0.0
    async with websockets.connect(url, additional_headers={"API-SUBSCRIPTION-KEY": os.environ["SARVAM_API_KEY"]},
                                  open_timeout=10, max_size=None) as ws:
        begun = asyncio.Event()
        ended = asyncio.Event()

        async def recv():
            async for raw in ws:
                m = json.loads(raw)
                ev = m.get("event")
                if ev == "session.begin":
                    begun.set()
                elif ev == "error":
                    events.append(("error", time.monotonic() - t0, json.dumps(m)[:200]))
                elif ev in ("vad.speech_start", "vad.speech_end", "transcript.final"):
                    events.append((ev, time.monotonic() - t0, (m.get("text") or "").strip()))
                elif ev == "session.end":
                    ended.set()
                    return

        rt = asyncio.create_task(recv())
        await asyncio.wait_for(begun.wait(), 10)
        step = int(sr * CHUNK_MS / 1000) * 2
        t0 = time.monotonic()
        pi = 0
        for i in range(0, len(pcm), step):
            pos = i / 2 / sr
            while manual and pi < len(plan) and plan[pi][1] <= pos:
                await ws.send(json.dumps({"event": "speech_start" if plan[pi][0] == "start" else "speech_end"}))
                pi += 1
            await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(pcm[i:i + step]).decode()}))
            lag = (i + step) / 2 / sr - (time.monotonic() - t0)
            if lag > 0:
                await asyncio.sleep(lag)
        while manual and pi < len(plan):
            await ws.send(json.dumps({"event": "speech_start" if plan[pi][0] == "start" else "speech_end"}))
            pi += 1
        await asyncio.sleep(1.0)  # let a trailing final arrive before closing the stream
        await ws.send(json.dumps({"event": "end"}))
        try:
            await asyncio.wait_for(ended.wait(), 6)
        except asyncio.TimeoutError:
            pass
        rt.cancel()

    finals = [(t, x) for e, t, x in events if e == "transcript.final" and x]
    if manual:
        starts = [t for e, t in plan if e == "start"]
        stops = [t for e, t in plan if e == "stop"]
    else:
        starts = [t for e, t, _ in events if e == "vad.speech_start"]
        stops = [t for e, t, _ in events if e == "vad.speech_end"]
    return {"text": " ".join(x for _, x in finals), "final_times": [t for t, _ in finals],
            "starts": starts, "stops": stops, "errors": [x for e, _, x in events if e == "error"]}


# ---------------------------------------------------------------- IndicConformer (local)
def _indic_clip(clip: dict, cfg: dict) -> dict:
    from agent.stt_indic import SAMPLE_RATE, transcribe

    from . import audio as A

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
            txt = transcribe(seg, cfg["indic_language"], cfg["indic_decoding"])
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
    if cfg["stt_provider"] == "indic":
        from agent.stt_indic import load_model, status

        st = status()
        if not st["ready"]:
            return {"skipped": st["message"]}, []
        await asyncio.to_thread(load_model)
        for c in clips:
            rows.append(score_clip(c, await asyncio.to_thread(_indic_clip, c, cfg)))
    else:
        sem = asyncio.Semaphore(concurrency)

        async def one(c):
            async with sem:
                for attempt in range(2):
                    try:
                        return score_clip(c, await _sarvam_clip(c, cfg))
                    except Exception as e:  # noqa: BLE001
                        err = f"{type(e).__name__}: {e}"
                        await asyncio.sleep(1 + attempt)
                return score_clip(c, {"text": "", "final_times": [], "starts": [], "stops": [], "errors": [err]})

        rows = list(await asyncio.gather(*(one(c) for c in clips)))
    return summarise(rows), rows
