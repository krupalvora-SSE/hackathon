"""Turn a long read-aloud recording of the script into a Piper training dataset.

    uv run --extra whisper python voice_training/prepare.py ~/Downloads/krupal-1.m4a ~/Downloads/krupal-2.m4a --name krupal

The script is voice_training/script_hinglish.txt (make_hinglish_script.py): Hinglish lines as the bot speaks
them. Several recordings are fine; pass them in reading order.

1. Decode to 22,050 Hz mono and cut at pauses (energy based).
2. Transcribe every piece with local Whisper (agent/stt_whisper.py; cached, so re-runs are instant).
3. Match pieces to script lines in reading order. A line read with a pause in it spans several
   pieces; stumbles and noise match nothing and are dropped; a re-read line keeps the last take.
4. Write data/<name>/wavs/<line>.wav (trimmed, loudness-evened) + metadata.csv (`0001.wav|text`, where
   text is to_devanagari(line): exactly what Piper is given at call time), report.md (what was kept /
   dropped and why) and data/<name>.zip for Colab.

Everything here runs on this machine; data/ is git-ignored. The zip leaves it only when you upload it
to Google Drive for the Colab training notebook.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from agent.devanagari import to_devanagari  # noqa: E402

SR = 22050          # Piper medium voices
FRAME = 0.02        # seconds per energy frame
MIN_PAUSE = 0.45    # a quieter stretch at least this long separates pieces
LONG_PIECE = 9.0    # longer pieces are probably several lines read back to back: re-cut at shorter pauses
SHORT_PAUSE = 0.2
MIN_PIECE = 0.35    # shorter bursts are clicks / breaths
PAD = 0.12          # silence kept around each clip
MATCH = 0.72        # similarity a piece (or run of pieces) needs to count as a script line
MAX_SPAN = 5        # most pieces one line may be split into
LOOK_BACK, LOOK_AHEAD = 3, 12  # script lines searched around the expected one

NUM = ("शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस "
       "इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस").split()


def load(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def pieces(x: np.ndarray, min_pause: float = MIN_PAUSE, offset: int = 0) -> list[tuple[int, int]]:
    """Speech stretches as (start, end) sample indices, cut at pauses."""
    hop = int(SR * FRAME)
    n = len(x) // hop
    db = 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    floor, loud = np.percentile(db, 10), np.percentile(db, 95)
    speech = db > max(floor + 10, loud - 35)
    out, start, quiet = [], None, 0
    for i, s in enumerate(speech):
        if s:
            start = i if start is None else start
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet * FRAME >= min_pause:
                end = i - quiet + 1
                if (end - start) * FRAME >= MIN_PIECE:
                    out.append((start * hop, end * hop))
                start, quiet = None, 0
    if start is not None and (n - start) * FRAME >= MIN_PIECE:
        out.append((start * hop, n * hop))
    out = [(a + offset, b + offset) for a, b in out]
    if min_pause > SHORT_PAUSE:  # re-cut run-on pieces at shorter pauses
        out = [p for a, b in out
               for p in ((pieces(x[a:b], SHORT_PAUSE, a) if (b - a) / SR > LONG_PIECE else None) or [(a, b)])]
    return out


def transcribe_all(x: np.ndarray, segs, cache_path: Path) -> list[str]:
    from scipy.signal import resample_poly

    from agent.stt_whisper import transcribe, warm_up

    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    warm_up()

    def one(seg):
        clip = x[seg[0]:seg[1]]
        key = hashlib.sha1(clip.tobytes()).hexdigest()[:16]
        if key not in cache:
            try:
                audio = resample_poly(clip.astype(np.float32), 16000, SR).astype(np.float32)
                cache[key] = transcribe(audio)[0]
            except Exception as e:  # noqa: BLE001 - give up on this piece, keep the rest
                print(f"  transcription failed for piece at {seg[0] / SR:.1f}s: {e}", file=sys.stderr)
                return ""
        return cache[key]

    texts = [one(seg) for seg in segs]  # one model on one GPU: in turn
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    return texts


def norm(t: str) -> str:
    t = to_devanagari(t, near_dates=False)
    t = re.sub(r"\d+", lambda m: NUM[int(m.group())] if int(m.group()) < len(NUM) else m.group(), t)
    t = t.replace("़", "").replace("ँ", "ं")       # nukta, chandrabindu: ASR is inconsistent
    t = t.replace("सोलार", "सोलर").replace("स्क्वायर", "स्क्वेयर").replace("कृपल", "कृपाल")  # ASR spellings
    return re.sub(r"[^ऀ-ॿ]", "", t).replace("।", "")


REANCHOR_AFTER = 5   # pieces in a row matching nothing near the expected line: search the whole script
REANCHOR_MATCH = 0.82  # ... but only jump on a confident match


def align(texts: list[str], script: list[tuple[str, str]], ptr: int = 0):
    """Greedy in reading order from script line `ptr` (carry it across recordings read in order).

    Returns ([(line_idx, first_piece, last_piece, score)], dropped piece idxs, next ptr). After
    REANCHOR_AFTER misses in a row it searches the whole script, so a skipped block or a recording
    that starts elsewhere doesn't lose every line after it."""
    lines = [norm(t) for _, t in script]
    hyp = [norm(t) for t in texts]
    out, dropped, i, misses = [], [], 0, 0

    def best_at(i: int, lo: int, hi: int):
        best = (0.0, None, None)
        for span in range(1, MAX_SPAN + 1):
            if i + span > len(hyp):
                break
            h = "".join(hyp[i:i + span])
            for j in range(lo, hi):
                if not 0.8 <= len(h) / max(1, len(lines[j])) <= 1.3:
                    continue             # half a line (or two lines) under one line's text: bad data
                sc = difflib.SequenceMatcher(None, h, lines[j], autojunk=False).ratio()
                if sc > best[0]:
                    best = (sc, span, j)
        return best

    while i < len(hyp):
        score, span, j = best_at(i, max(0, ptr - LOOK_BACK), min(len(lines), ptr + LOOK_AHEAD))
        if not (span and score >= MATCH) and misses >= REANCHOR_AFTER:
            g = best_at(i, 0, len(lines))
            if g[1] and g[0] >= REANCHOR_MATCH:
                score, span, j = g
        if span and score >= MATCH:
            out.append((j, i, i + span - 1, score))
            i, ptr, misses = i + span, j + 1, 0
        else:
            dropped.append(i)
            i += 1
            misses += 1
    return out, dropped, ptr


def tidy(clip: np.ndarray) -> np.ndarray:
    clip = clip - clip.mean()
    rms = np.sqrt((clip ** 2).mean()) + 1e-9
    clip = clip * (10 ** (-20 / 20) / rms)                 # about -20 dBFS
    peak = np.abs(clip).max()
    return clip * (0.95 / peak) if peak > 0.95 else clip


def main():
    global MATCH
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", nargs="+", type=Path, help="recordings, in the order they were read")
    ap.add_argument("--name", required=True, help="speaker name, used for the output folder")
    ap.add_argument("--min-match", type=float, default=MATCH,
                    help=f"similarity a clip needs to be kept (default {MATCH}). Raise to ~0.8 when many script lines "
                         "share a template (only the name/date differs), so a clip can't take a sibling line's text")
    ap.add_argument("--script", type=Path, default=HERE / "script_hinglish.txt",
                    help="script that was read (default: the Hinglish one; script_hi.txt for the older pure-Hindi set)")
    a = ap.parse_args()
    MATCH = a.min_match

    from dotenv import load_dotenv
    load_dotenv(HERE.parent / ".env")

    out = HERE / "data" / a.name
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    for old in (out / "wavs").glob("*.wav"):
        old.unlink()
    script = [tuple(l.split("|", 1)) for l in a.script.read_text().splitlines() if "|" in l]

    kept: dict[int, tuple] = {}
    dropped_log, total, ptr = [], 0.0, 0
    for path in a.audio:
        print(f"{path.name}: decoding")
        x = load(path.expanduser())
        total += len(x) / SR
        segs = pieces(x)
        print(f"  {len(segs)} pieces, transcribing with local Whisper")
        texts = transcribe_all(x, segs, out / "stt_cache.json")
        matches, dropped, ptr = align(texts, script, ptr)  # files continue the reading order
        for j, f, l, score in matches:
            if j in kept:
                dropped_log.append((path.name, segs[kept[j][1]][0] / SR, "re-read later, kept the last take",
                                    script[j][1]))
            kept[j] = (path, f, l, score, x, segs, texts)
        for d in dropped:
            dropped_log.append((path.name, segs[d][0] / SR, "matched no script line", texts[d]))

    meta, secs = [], 0.0
    pad = int(PAD * SR)
    for j in sorted(kept):
        path, f, l, score, x, segs, texts = kept[j]
        clip = tidy(x[max(0, segs[f][0] - pad): min(len(x), segs[l][1] + pad)].copy())
        lid, text = script[j]
        sf.write(out / "wavs" / f"{lid}.wav", clip, SR, subtype="PCM_16")
        meta.append(f"{lid}.wav|{to_devanagari(text, near_dates=False)}")  # exactly what Piper gets at call time
        secs += len(clip) / SR
    (out / "metadata.csv").write_text("\n".join(meta) + "\n")

    with zipfile.ZipFile(HERE / "data" / f"{a.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(out / "metadata.csv", "metadata.csv")
        for w in sorted((out / "wavs").glob("*.wav")):
            z.write(w, f"wavs/{w.name}")

    got = sorted(int(script[j][0]) for j in kept)
    weak = [(script[j][0], kept[j][3]) for j in sorted(kept) if kept[j][3] < 0.8]
    rep = [f"# Voice dataset: {a.name}", "",
           f"- Recorded: {total / 60:.1f} min in {len(a.audio)} file(s)",
           f"- Kept: **{len(kept)} of {len(script)} script lines**, {secs / 60:.1f} min of clips",
           f"- Lines covered: {got[0] if got else '-'} to {got[-1] if got else '-'}",
           f"- Missing inside that range: {', '.join(str(n) for n in range(got[0], got[-1] + 1) if n not in got) or 'none'}"
           if got else "", "",
           "## Kept but worth a listen (match below 0.80)", "",
           *[f"- {lid}: {s:.2f}" for lid, s in weak], "",
           "## Dropped pieces", "",
           *[f"- {f} @ {t // 60:.0f}:{t % 60:04.1f}: {why}: {txt}" for f, t, why, txt in dropped_log]]
    (out / "report.md").write_text("\n".join(rep) + "\n")
    print("\n".join(rep[:6]))
    print(f"\nDataset: {out}\nZip for Colab: {HERE / 'data' / (a.name + '.zip')}\nReport: {out / 'report.md'}")


if __name__ == "__main__":
    main()
