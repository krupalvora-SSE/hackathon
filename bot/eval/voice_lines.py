"""Does the voice say what each prompt line says, and nothing more? A listening list for every flow line.

    cd bot && uv run --extra whisper python -m eval.voice_lines                 # voice from config.json
    uv run --extra whisper python -m eval.voice_lines --voice hi_IN-krupal-medium --lang hi --all

Each line of flows/<flow>/prompts/<lang>.yaml (placeholders filled as on a call) goes through the live text path
(to_devanagari + the TTS normalizer) and the Piper voice, then local Whisper. A line is flagged when the audio has
letters at either end that the text doesn't (a word the voice adds) or the text has letters the audio lacks (a
word it drops): the same check voice_training/prepare.py uses on training clips. CALL-3f18802d: "माफ़ कीजिए, ठीक
से सुनाई नहीं दिया।" came out with "बढ़िया" on the end, learned from a training clip that held the next line's word.

Whisper mishears a synthetic voice in the middle of lines too; that is not flagged. Listen to what is flagged
(--out writes each flagged line as a WAV) before changing a prompt.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np

BOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT / "voice_training"))

SAMPLE_BOOKING = {
    "id": "BK-EVAL", "customerName": "Rahul Joshi", "phone": "+910000000001",
    "beatPlan": {"primary_slot": {"date": "2026-11-28", "band": "morning", "hero_id": "SH-7"},
                 "backup_slots": [{"date": "2026-12-03", "band": "evening", "hero_id": "SH-7"},
                                  {"date": "2026-11-30", "band": "morning", "hero_id": "SH-2"}],
                 "availability": {"2026-11-28": ["morning", "afternoon"]}},
}


def lines(lang: str) -> list[tuple[str, str, str]]:
    """(state, key, text) for every prompt line, placeholders filled from a sample call."""
    from datetime import date

    from agent.flow import FlowCall, FlowSpec

    call = FlowCall(call_id="EVAL", booking=SAMPLE_BOOKING, spec=FlowSpec.load(), today=date(2026, 11, 26))
    call.lang = lang
    call.start()
    call.offered = list(call.context.get("backup_slots") or [])[:2]
    call.ctx.update(hold_until="2027-01-15", missing_items=["ladder"], recall_window="kal subah 10 baje")
    out = []
    for state, keys in call.spec.prompts[lang].items():
        for key in keys or {}:
            text = call._global_line(key) if state == "_global" else call._line(state, key)
            if text.strip():
                out.append((state, key, text))
    return out


def main() -> None:
    from scipy.signal import resample_poly

    from agent import config as call_config
    from agent.devanagari import devanagari_transform
    from agent.stt_whisper import transcribe_detailed
    from agent.voice_local import _synth_fn, load_piper_voice
    from prepare import OVERRUN, norm, overrun

    cfg = call_config.load()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", default=cfg["piper_voice"])
    ap.add_argument("--lang", default="hi")
    ap.add_argument("--all", action="store_true", help="print every line, not only the flagged ones")
    ap.add_argument("--out", type=Path, help="write each flagged line's audio here as <state>.<key>.wav")
    a = ap.parse_args()

    voice = load_piper_voice(a.voice)
    synth, sr = _synth_fn(voice), voice.config.sample_rate
    flagged = []
    todo = lines(a.lang)
    for state, key, text in todo:
        spoken = asyncio.run(devanagari_transform(text))
        pcm = np.frombuffer(synth(spoken), dtype=np.int16).astype(np.float32) / 32768
        heard = transcribe_detailed(resample_poly(pcm, 16000, sr).astype(np.float32), prompt=None)["text"]
        extra = overrun(norm(heard), norm(spoken))
        bad = extra >= OVERRUN
        if bad:
            flagged.append({"line": f"{state}.{key}", "text": spoken, "heard": heard, "overrun": extra})
            if a.out:
                from eval.audio import write_wav
                a.out.mkdir(parents=True, exist_ok=True)
                write_wav(a.out / f"{state}.{key}.wav", pcm, sr)
        if bad or a.all:
            print(f"{'FLAG' if bad else 'ok  '} {extra:2d} {state}.{key}\n     said:  {spoken}\n     heard: {heard}")
    print(json.dumps({"voice": a.voice, "lines": len(todo), "flagged": len(flagged)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
