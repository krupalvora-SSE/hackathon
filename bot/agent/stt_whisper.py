"""Local STT with OpenAI Whisper large-v3-turbo: open source, no account, token or credits needed.

On Apple Silicon it runs on the GPU through MLX (~1.3 s for a 5 s Hindi line on an M4); elsewhere
through faster-whisper on the CPU (~6 s for the same line: too slow for live calls without a GPU).
It is not streaming: the local Silero VAD cuts the customer's speech into
segments and each is transcribed whole. A reply is transcribed as English when Whisper is
sure it is English (a customer may switch); everything else, including short Hindi replies it
mistakes for Japanese/Korean/Urdu, is transcribed as Hindi, so Hindi always comes back in Devanagari.

Setup (once): uv sync --extra whisper. The model (~1.6 GB) must already be in the local model cache;
the bot runs with HF_HUB_OFFLINE=1 and never downloads it.
"""
from __future__ import annotations

import asyncio
import json
import re
import os
import wave
from pathlib import Path
import importlib.util
import platform
import threading
import time
from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601
from pipecat.utils.tracing.service_decorators import traced_stt

MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
CPU_MODEL = "deepdml/faster-whisper-large-v3-turbo-ct2"
NO_SPEECH_PROB = 0.6  # drop segments Whisper thinks are noise (stops "धन्यवाद" hallucinated from a cough)
EN_MIN_PROB = 0.6     # CPU path: transcribe as English only above this; everything else is Hindi
EN_PLAUSIBLE = 0.3    # MLX path: also try English when Whisper gives it at least this much
EN_MARGIN = 0.15      # ... and keep English only if its avg log-prob beats Hindi's by this much
# Unsure transcript: the call asks the customer again instead of acting on it (agent/bot.py).
# Calibrated on eval/corpus (224 speech clips + noise, with VOCAB_PROMPT): at -0.35 it flags 19% of wrong
# transcripts and every noise clip, and 1% (1/123) of right ones. Whisper's stock -1.0 flagged none here.
UNSURE_LOGPROB = float(os.getenv("WHISPER_UNSURE_LOGPROB", "-0.35"))
UNSURE_COMPRESSION = 2.4    # gzip ratio above this: a repetition loop ("कर दो कर दो कर दो ...")
# What Whisper writes for noise, echo or silence (measured on eval/corpus noise clips and seen on live
# calls): dropped as if nothing was said, so the bot doesn't answer a cough.
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "झाल", "धन्यवाद",
                  "सब्सक्राइब", "subscribe", "you", "bye", "the end", "हम्म"}
# Words customers say on this call, given to Whisper as prior context (its `prompt`). Short, in the
# script Whisper should answer in; it nudges "हजी" towards "हाँ जी" and "झाल" towards real words.
VOCAB_PROMPT = os.getenv("WHISPER_PROMPT", "हाँ जी, ठीक है, नहीं जी, चलेगा, कर दो। शनिवार सुबह, सोमवार शाम। "
                                           "सीढ़ी है, पानी है, light है। SolarSquare, slot, confirm.")


def call_prompt(name: str | None) -> str:
    """VOCAB_PROMPT led by "नमस्ते जी, <name> जी।" for this call (CALL-786626d2, re-decoded): the greeting came back
    "नमस्टे" (unsure) -> "नमस्ते"; "विकार दिसे बाती" -> "विकास जी से …". The name must be in Devanagari: with the
    Latin "Vikas" Whisper answered "Namaste" in Roman script, unsure. "solar plant" / "wrong number" in the prompt
    made it drop the "नहीं" from "कोई सोलर प्लांट नहीं है", so they are left out."""
    from .devanagari import to_devanagari

    first = str(name or "").split()[:1]
    lead = f"नमस्ते जी, {to_devanagari(first[0], near_dates=False)} जी।" if first else "नमस्ते जी।"
    return f"{lead} {VOCAB_PROMPT}"

# Whole replies Whisper keeps writing for short answers it half-hears (live calls, browser mic): the
# "h" and nasal of "हाँ जी" get lost and it comes back as "अजी"/"आजी" however it is decoded; "confirm"
# comes back as "कन फो". Only an exact whole-reply match is replaced, so a real "अजी सुनिए" is untouched.
MISHEARD = {**dict.fromkeys(("अजी", "आजी", "आँजी", "अँजी", "आंजी", "अंजी", "हजी", "हाजी", "हांजी", "हाँजी",
                             "हा जी", "हां जी", "aji", "aaji", "haji", "hanji", "haanji", "han ji"), "हाँ जी"),
            **dict.fromkeys(("कन फो", "कन फोल्म", "कन फॉर्म", "कनफो", "कन्फो", "कन्फोर्म", "कन्फ़ॉर्म",
                             "कन्फॉर्म", "conform", "con four"), "confirm"),
            # "एक भी नहीं" (none of them) said fast: the "भ" and nasal "नहीं" collapse
            **dict.fromkeys(("एक बिने", "एक बीने", "एक भीने", "एक भिने", "एक भी ने", "एक बी नहीं", "एक बिनी",
                             "एक भी नई", "ek bine", "ek bhine"), "एक भी नहीं")}

# Short answers Whisper garbles when decoding freely ("हाँ" came back as "अँ़", "बढ़ी", "अ" on browser-mic
# calls). For an unsure reply of at most two words, each of these is scored against the audio and the
# best is kept when it is likely enough (SHORT_MIN_LOGPROB) and its side (yes / not yes) wins by
# SHORT_MARGIN. Calibrated on recordings/ + eval/corpus: at -0.9 no "नहीं" clip turns into a yes
# (one noisy synthetic "नहीं" scores "हाँ" at -1.03); below it the reply stays unsure and is asked again.
SHORT_YES = ("हाँ", "हाँ जी", "जी", "जी हाँ", "हाँ हाँ", "हाँ बोलिए", "ठीक है", "ओके")
SHORT_OTHER = ("नहीं", "नहीं जी", "ना", "हेलो", "कौन", "नमस्ते")   # नमस्ते: "नमस्टे" came back unsure (CALL-786626d2)
SHORT_MIN_LOGPROB = -0.9
SHORT_MARGIN = 0.25
# "क्या आप फिर से दोहराएंगे?" / "सुनाई नहीं दिया" garbled on its own ("शलिसे, नहाँ है।", CALL-3f18802d). An unsure
# reply of at most REPEAT_MAX_WORDS words is also scored against these; a repeat phrase is kept when it beats every
# answer by REPEAT_MARGIN. Loose on purpose: a wrong pick only makes the bot say its question again.
# Calibrated on recordings/ + eval/corpus (113 unsure short clips): only two prefer a repeat phrase, both real
# repeat requests: CALL-3f18802d/006 (+0.17 over "ठीक है") and CALL-47973406/008 "सुना इनिदी आ" (+1.01).
SHORT_REPEAT = ("फिर से दोहराएंगे", "फिर से बोलिए", "सुनाई नहीं दिया", "क्या बोला", "दोबारा बोलिए", "समझ नहीं आया")
SHORT_ANSWERS = ("हाँ है", "नहीं है", "पता नहीं", "हाँ जी", "नहीं जी", "ठीक है")
REPEAT_MAX_WORDS = 4
REPEAT_MIN_LOGPROB = -1.5
REPEAT_MARGIN = 0.15
# A segment after a mid-sentence pause is decoded together with the turn's earlier segments (see run_stt).
JOIN_MAX_S = 20.0
JOIN_GAP_S = 0.25

_warm = threading.Lock()
_warmed = False
# One MLX decode at a time: MLX inference is not thread-safe. server.py warms Whisper on a background thread at
# import; a decode running beside it came back empty after ~40 s or crashed the process (seen in the test suite;
# two calls at once would hit the same).
_mlx_lock = threading.Lock()
# When the Mac is short of RAM, macOS pages Whisper's ~1.6 GB of weights out while the bot sits idle; the next
# decode then pages them back in: CALL-786626d2's first "नमस्टे" took 32 s (process RSS was 200 MB, swap 11 GB
# used) and the customer's next three lines queued behind it. A tiny decode every KEEP_WARM_S keeps them resident.
KEEP_WARM_S = 20.0
# MLX keeps freed GPU buffers in a cache with no limit by default: every decode shape (prompt + reply length,
# rescoring candidates) left more behind, and the bot process reached 11 GB after one call (CALL-26e8229e) on a
# 16 GB Mac, so macOS swapped the model out within 30 s and the first reply took 15 s. Capped, and cleared after
# each decode: the process stays at the model (~1.6 GB) plus one decode's working memory.
MLX_CACHE_MB = int(os.getenv("WHISPER_MLX_CACHE_MB", "256"))
_cache_capped = False


def _cap_mlx_cache() -> None:
    global _cache_capped
    if not _cache_capped:
        import mlx.core as mx

        mx.set_cache_limit(MLX_CACHE_MB << 20)
        _cache_capped = True


def mlx_memory() -> dict:
    """MB the MLX allocator holds: active (model + live arrays), cache (freed, kept for reuse), peak."""
    import mlx.core as mx

    return {k: round(getattr(mx, f"get_{k}_memory")() / 2**20) for k in ("active", "cache", "peak")}
_last_decode = 0.0
_keeper: threading.Thread | None = None


def use_mlx() -> bool:
    return (platform.system() == "Darwin" and platform.machine() == "arm64"
            and importlib.util.find_spec("mlx_whisper") is not None)


def status() -> dict:
    """Is local Whisper usable here? Shown under the STT switch on the /voice page."""
    mlx = use_mlx()
    installed = mlx or importlib.util.find_spec("faster_whisper") is not None
    if not installed:
        msg = "Not installed: run `uv sync --extra whisper` in bot/."
    elif mlx:
        msg = "Ready: Apple GPU (MLX), about 1 s per reply. Loads from the local model cache."
    else:
        msg = "Ready on CPU only: about 6 s per reply, too slow for live calls without a GPU."
    return {"installed": installed, "ready": installed, "backend": "mlx" if mlx else "cpu", "message": msg}


def warm_up() -> None:
    """Load the model once per process so the customer's first reply isn't slow. Blocking."""
    global _warmed
    with _warm:
        if _warmed:
            return
        import numpy as np

        if use_mlx():
            # the call's own path (shared encoder pass, prompted Hindi decode): mlx compiles kernels per
            # shape on first use, so warming with mlx_whisper.transcribe left the first reply at ~5.5 s
            rng = np.random.default_rng(0)
            for secs in (1.5, 3.0):
                transcribe_detailed((rng.standard_normal(int(16000 * secs)) * 0.01).astype(np.float32), mlx=True)
        else:
            _cpu_model()
        _warmed = True
        _start_keeper()
        logger.info(f"Whisper ready ({'MLX ' + MLX_MODEL if use_mlx() else 'CPU ' + CPU_MODEL})")


def refresh() -> float | None:
    """One short decode so the weights are back in RAM, unless a real decode is running (it does the same).
    Seconds taken, or None when skipped. Blocking; call it on a thread."""
    global _last_decode
    if not use_mlx() or not _warmed or not _mlx_lock.acquire(blocking=False):
        return None
    try:
        import numpy as np

        t = time.monotonic()
        _cap_mlx_cache()
        _transcribe_mlx((np.random.default_rng(0).standard_normal(16000) * 0.01).astype(np.float32), VOCAB_PROMPT)
        import mlx.core as mx

        mx.clear_cache()
        _last_decode = time.monotonic()
        secs = _last_decode - t
        if secs > 3:
            logger.warning(f"Whisper took {secs:.1f}s to page its weights back in: the Mac is short of RAM")
        return secs
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Whisper keep-warm decode failed: {e!r}")
        return None
    finally:
        _mlx_lock.release()


def _keep_warm_loop() -> None:
    while True:
        time.sleep(KEEP_WARM_S / 2)
        if time.monotonic() - _last_decode >= KEEP_WARM_S:
            refresh()


def _start_keeper() -> None:
    global _keeper
    if use_mlx() and _keeper is None:
        _keeper = threading.Thread(target=_keep_warm_loop, name="whisper-keep-warm", daemon=True)
        _keeper.start()


def _keep(text: str) -> str:
    """The transcript, or "" when it is only one of Whisper's noise phrases."""
    text = text.strip()
    bare = re.sub(r"[।॥.,!?;:'\"()\-]", " ", text).lower()
    return "" if " ".join(bare.split()) in HALLUCINATIONS else text


LOOP_FILLERS = {"जी", "जि", "ji", "हम्म", "हम", "हं", "अ", "आ", "ह", "उम", "hmm", "um", "a"}


def _unloop(text: str, ratio: float) -> str:
    """A repetition loop with repeats collapsed ("नहीं नहीं नहीं" -> "नहीं"); "" when a filler sound looped
    ("जी, जी, जी, ... ज"): Whisper running on noise or a breath, not an answer (CALL-980a5cd0 read it as a yes)."""
    if ratio <= UNSURE_COMPRESSION:
        return text
    words = re.sub(r"[।॥.,!?;:'\"()\-]", " ", text).split()
    out: list[str] = []
    for w in words:
        if not out or w != out[-1]:
            out.append(w)
    if len(out) > 1 and out[-1] != words[-1] or (len(out) > 1 and out[-2].startswith(out[-1])):
        out.pop()                                     # the loop cut off mid-word: "जी ज"
    if len(out) == 1 and out[0].lower() in LOOP_FILLERS:
        return ""
    return " ".join(out)


# Single words Whisper misspells in otherwise fine Hindi; fixed in place (the transcript's confidence is unchanged).
WORD_FIXES = {"नमस्टे": "नमस्ते", "नमस्टें": "नमस्ते", "नमश्ते": "नमस्ते", "सोलार": "सोलर", "प्लान्ट": "प्लांट",
              "प्लैंट": "प्लांट", "रॉंग": "wrong", "रोंग": "wrong", "रॉन्ग": "wrong", "रांग": "wrong"}
_WORD_FIX = re.compile("|".join(sorted(map(re.escape, WORD_FIXES), key=len, reverse=True)))


def _fix(r: dict) -> dict:
    """Replace a known whole-reply mishearing (MISHEARD); such a fix is not an unsure transcript.
    Known misspelt words (WORD_FIXES) are corrected inside any reply."""
    bare = " ".join(re.sub(r"[।॥.,!?;:'\"()\-]", " ", r["text"]).lower().split())
    if bare in MISHEARD:
        logger.debug(f"Whisper [{r['text']}] read as [{MISHEARD[bare]}]")
        return {**r, "text": MISHEARD[bare], "heard_as": r["text"], "fixed": True}
    fixed = _WORD_FIX.sub(lambda m: WORD_FIXES[m.group(0)], r["text"])
    return {**r, "text": fixed, "heard_as": r["text"]} if fixed != r["text"] else r


def is_unsure(r: dict) -> bool:
    """True when Whisper itself isn't confident in the transcript (see UNSURE_*)."""
    if r.get("fixed"):
        return False
    return bool(r["text"]) and (r["avg_logprob"] < UNSURE_LOGPROB or r["compression_ratio"] > UNSURE_COMPRESSION)


def transcribe(audio, mlx: bool | None = None) -> tuple[str, str]:
    """16 kHz float32 mono -> (text, "hi" | "en"). Blocking; call off the event loop."""
    r = transcribe_detailed(audio, mlx)
    return r["text"], r["lang"]


def transcribe_detailed(audio, mlx: bool | None = None, prompt: str | None = VOCAB_PROMPT) -> dict:
    """16 kHz float32 mono -> {text, lang, avg_logprob, compression_ratio, no_speech_prob}. Blocking."""
    mlx = use_mlx() if mlx is None else mlx
    if mlx:
        global _last_decode
        with _mlx_lock:
            _cap_mlx_cache()
            try:
                return _fix(_transcribe_mlx(audio, prompt))
            finally:
                _last_decode = time.monotonic()
                import mlx.core as mx

                mx.clear_cache()

    def run(language):
        segs, info = _cpu_model().transcribe(audio, language=language, beam_size=1, condition_on_previous_text=False,
                                             initial_prompt=prompt if language == "hi" else None)
        return list(segs), info

    _, info = _cpu_model().transcribe(audio, language=None, beam_size=1, condition_on_previous_text=False)
    lang = "en" if info.language == "en" and info.language_probability >= EN_MIN_PROB else "hi"
    segs, _ = run(lang)
    kept = [s for s in segs if s.no_speech_prob < NO_SPEECH_PROB]
    return _fix({"text": _keep("".join(s.text for s in kept)), "lang": lang,
                 "avg_logprob": min((s.avg_logprob for s in kept), default=0.0),
                 "compression_ratio": max((s.compression_ratio for s in kept), default=0.0),
                 "no_speech_prob": max((s.no_speech_prob for s in segs), default=1.0)})


def _transcribe_mlx(audio, prompt: str | None = None) -> dict:
    """One encoder pass shared by language detection and decoding (mlx_whisper.transcribe with no
    language encodes twice: ~1.9 s instead of ~1.1 s a reply). Replies are under Whisper's 30 s window."""
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.decoding import DecodingOptions, decode, detect_language
    from mlx_whisper.transcribe import ModelHolder

    model = ModelHolder.get_model(MLX_MODEL, mx.float16)
    mel = pad_or_trim(log_mel_spectrogram(audio, n_mels=model.dims.n_mels)[:N_FRAMES], N_FRAMES, axis=-2)
    features = model.encoder(mel[None].astype(mx.float16))
    _, probs = detect_language(model, features)
    max_tokens = min(224, int(len(audio) / 16000 * 20) + 20)  # a repetition loop can't run for a second

    def run(language):
        r = decode(model, features, DecodingOptions(language=language, temperature=0.0, without_timestamps=True,
                                                    fp16=True, sample_len=max_tokens,
                                                    prompt=prompt if language == "hi" and prompt else None))
        return r[0] if isinstance(r, list) else r

    # Language detection alone is unreliable on short replies: "हाँ जी" / "ठीक है" are taken for
    # Japanese or Korean, and an accented Hindi sentence can score en=0.67 (and forced to English,
    # Whisper *translates* it). So decode Hindi always, English only when it is plausible, and keep
    # English only if it is clearly more confident or the Hindi attempt is a repetition loop.
    hi = run("hi")
    if prompt and hi.compression_ratio > UNSURE_COMPRESSION:
        # a repetition loop seeded by the vocab prompt ("confirm" -> "कर दो, कर दो, ..."): decode unprompted
        bare = decode(model, features, DecodingOptions(
            language="hi", temperature=0.0, without_timestamps=True, fp16=True, sample_len=max_tokens))
        bare = bare[0] if isinstance(bare, list) else bare
        if bare.compression_ratio <= UNSURE_COMPRESSION:
            hi = bare
    best, lang = hi, "hi"
    short = _short_answer(model, features, prompt, hi) or _repeat_request(model, features, prompt, hi)
    if short:
        return short
    if probs[0].get("en", 0) >= EN_PLAUSIBLE:
        en = run("en")
        if en.avg_logprob > hi.avg_logprob + EN_MARGIN or (hi.compression_ratio > 2.4 >= en.compression_ratio):
            best, lang = en, "en"
    return {"text": _keep(_unloop(best.text, best.compression_ratio)) if best.no_speech_prob < NO_SPEECH_PROB else "",
            "lang": lang,
            "avg_logprob": float(best.avg_logprob), "compression_ratio": float(best.compression_ratio),
            "no_speech_prob": float(best.no_speech_prob)}


def _scores(model, features, prompt: str | None, cands: tuple[str, ...]) -> dict[str, float]:
    """Mean log-prob of each candidate transcript (incl. end of text, as Whisper scores) for this audio."""
    import mlx.core as mx
    import numpy as np
    from mlx_whisper.tokenizer import get_tokenizer

    tok = get_tokenizer(model.is_multilingual, num_languages=model.num_languages, language="hi", task="transcribe")
    pre = ([tok.sot_prev] + tok.encode(" " + prompt.strip()) if prompt else []) \
        + list(tok.sot_sequence_including_notimestamps)
    seqs = [pre + tok.encode(" " + c) + [tok.eot] for c in cands]
    width = max(map(len, seqs))
    batch = mx.array([q + [tok.eot] * (width - len(q)) for q in seqs])
    logits, _, _ = model.decoder(batch[:, :-1], mx.repeat(features, len(seqs), axis=0))
    lp = logits.astype(mx.float32)
    lp = np.array(lp - mx.logsumexp(lp, axis=-1, keepdims=True))
    scores = {}
    for i, (c, q) in enumerate(zip(cands, seqs)):
        pos = np.arange(len(pre) - 1, len(q) - 1)
        scores[c] = float(lp[i, pos, q[len(pre):]].sum()) / (len(q) - len(pre))
    return scores


def _garbled(hi, max_words: int) -> str | None:
    """The text of an unsure, short, Devanagari-only decode (worth rescoring), else None."""
    text = hi.text.strip()
    if not text or len(text.split()) > max_words or re.search(r"[A-Za-z]", text) or hi.no_speech_prob >= NO_SPEECH_PROB:
        return None
    if not is_unsure({"text": text, "avg_logprob": hi.avg_logprob, "compression_ratio": hi.compression_ratio}):
        return None
    return text


def _short_answer(model, features, prompt: str | None, hi) -> dict | None:
    """The likeliest SHORT_YES/SHORT_OTHER answer for a garbled short reply, or None (see SHORT_*)."""
    text = _garbled(hi, 2)
    if text is None:
        return None
    scores = _scores(model, features, prompt, SHORT_YES + SHORT_OTHER)
    pick = max(scores, key=scores.get)
    side = SHORT_YES if pick in SHORT_YES else SHORT_OTHER
    rival = max(v for c, v in scores.items() if c not in side)
    if scores[pick] < SHORT_MIN_LOGPROB or scores[pick] - rival < SHORT_MARGIN:
        return None
    logger.debug(f"Whisper [{text}] ({hi.avg_logprob:.2f}) rescored as [{pick}] ({scores[pick]:.2f})")
    return {"text": pick, "lang": "hi", "avg_logprob": scores[pick], "compression_ratio": 0.0,
            "no_speech_prob": float(hi.no_speech_prob), "heard_as": text, "fixed": True}


def _repeat_request(model, features, prompt: str | None, hi) -> dict | None:
    """A garbled short reply that is most likely "please repeat" (see SHORT_REPEAT), else None."""
    text = _garbled(hi, REPEAT_MAX_WORDS)
    if text is None:
        return None
    scores = _scores(model, features, prompt, SHORT_REPEAT + SHORT_ANSWERS + SHORT_YES + SHORT_OTHER)
    pick = max(SHORT_REPEAT, key=scores.get)
    rival = max(v for c, v in scores.items() if c not in SHORT_REPEAT)
    if scores[pick] < REPEAT_MIN_LOGPROB or scores[pick] - rival < REPEAT_MARGIN:
        return None
    logger.debug(f"Whisper [{text}] ({hi.avg_logprob:.2f}) rescored as repeat [{pick}] ({scores[pick]:.2f})")
    return {"text": pick, "lang": "hi", "avg_logprob": scores[pick], "compression_ratio": 0.0,
            "no_speech_prob": float(hi.no_speech_prob), "heard_as": text, "fixed": True}


def _norm_words(text: str) -> list[str]:
    return re.sub(r"[।॥.,!?;:'\"()\-]", " ", text).lower().split()


def continuation(earlier: str, joined: dict) -> dict | None:
    """The new words of a turn decoded whole: `joined` minus the `earlier` segments' text it starts with.
    None when the joined decode doesn't start with what was already sent (then the segment is decoded alone)."""
    tokens = (joined.get("text") or "").split()
    before = _norm_words(earlier)
    seen: list[str] = []
    for i, tok in enumerate(tokens):
        if seen == before:
            rest = " ".join(tokens[i:]).strip()
            return {**joined, "text": rest, "turn_text": joined["text"], "joined": True} if rest else None
        seen += _norm_words(tok)
        if seen != before[:len(seen)]:
            return None
    return None


_cpu = None


def _cpu_model():
    global _cpu
    if _cpu is None:
        from faster_whisper import WhisperModel

        _cpu = WhisperModel(CPU_MODEL, device="cpu", compute_type="int8")
    return _cpu


RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"  # git-ignored, stays on this machine


class WhisperHiEnSTTService(SegmentedSTTService):
    """pipecat STT service: local Whisper, Hindi or English per reply. Needs VAD in the pipeline.

    turn_unsure: True once any segment of the customer's current turn was an unsure transcript;
    agent/bot.py resets it when a turn starts and asks again instead of acting on such a turn.
    With record=True every segment is saved as recordings/<call_id>/NNN.wav, with what Whisper
    made of it in segments.jsonl, to diagnose mishearing on real calls.
    """

    def __init__(self, *, call_id: str | None = None, record: bool = False, prompt: str = VOCAB_PROMPT, **kwargs):
        super().__init__(**kwargs)
        self._prompt = prompt
        self.turn_unsure = False
        self.turn_heard: list[dict] = []
        self.turn_consumed = True
        self._turn_audio: list[bytes] = []   # this turn's earlier segments, re-decoded with the next one
        self._dir = RECORDINGS / call_id if (record and call_id) else None
        self._n = 0

    def start_turn(self) -> None:
        """A new customer turn. What was heard is kept until the LLM has acted on it (turn_consumed): when the
        customer goes on talking before the reply, the LLM reads all of it, so every segment's doubt must count
        (CALL-2c3e81bb: three unsure segments were wiped here and their text was read as do-not-call)."""
        if self.turn_consumed:
            self.turn_unsure = False
            self.turn_heard = []
            self._turn_audio = []
            self.turn_consumed = False

    def consume_turn(self) -> None:
        """The LLM has acted on what was heard; the next turn starts clean."""
        self.turn_consumed = True

    @property
    def wants_wav_segments(self) -> bool:
        return False  # raw 16-bit PCM

    @property
    def segment_sample_rate(self) -> int:
        return 16000

    def can_generate_metrics(self) -> bool:
        return True

    @traced_stt
    async def _handle_transcription(self, transcript: str, is_final: bool, language: Language | None = None):
        """Tracing hook for a final transcript (pipecat services each declare their own)."""

    def _record(self, audio: bytes, r: dict, unsure: bool, secs: float) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._n += 1
            name = f"{self._n:03d}.wav"
            with wave.open(str(self._dir / name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(audio)
            with open(self._dir / "segments.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"file": name, "audio_s": round(len(audio) / 32000, 2), "stt_s": round(secs, 2),
                                    "unsure": unsure, **{k: (round(v, 3) if isinstance(v, float) else v)
                                                         for k, v in r.items()}}, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"could not record STT segment: {e}")

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        import numpy as np

        def pcm(b: bytes):
            return np.frombuffer(b, dtype=np.int16).astype(np.float32) / 32768.0

        await self.start_processing_metrics()
        t = time.monotonic()
        joined = None
        try:
            prev = [] if self.turn_consumed else self._turn_audio
            if prev and self.turn_heard and sum(map(len, prev)) + len(audio) <= JOIN_MAX_S * 32000:
                # The customer paused mid-sentence and VAD cut it ("क्या आप, | फिर से दोहराएंगे?"): alone, the
                # second half came back as "शलिसे, नहाँ है।" (CALL-3f18802d); decoded with the first half it is
                # exact. Whisper pads every input to 30 s, so the joined decode costs about the same.
                gap = bytes(2 * int(16000 * JOIN_GAP_S))
                rj = await asyncio.to_thread(transcribe_detailed, pcm(b"".join(p + gap for p in prev) + audio),
                                             None, self._prompt)
                joined = continuation(" ".join(h["text"] for h in self.turn_heard), rj)
            r = joined or await asyncio.to_thread(transcribe_detailed, pcm(audio), None, self._prompt)
        except Exception as e:  # noqa: BLE001
            logger.exception("Whisper transcription failed")
            yield ErrorFrame(f"Whisper: {e}")
            return
        finally:
            await self.stop_processing_metrics()
        self._turn_audio = [*([] if self.turn_consumed else self._turn_audio), audio]
        text, lang, unsure = r["text"], r["lang"], is_unsure(r)
        secs = time.monotonic() - t
        if self._dir:
            self._record(audio, r, unsure, secs)
        if joined:
            # the whole turn was decoded again: its confidence replaces the pieces' (they were guesses)
            logger.debug(f"Whisper turn re-decoded joined: [{r['turn_text']}]")
            self.turn_heard = [{"text": r["turn_text"], "unsure": unsure}]
            self.turn_unsure = unsure
            if text:
                await self._handle_transcription(text, True, Language.EN_IN if lang == "en" else Language.HI_IN)
                yield TranscriptionFrame(text, self._user_id, time_now_iso8601(),
                                         Language.EN_IN if lang == "en" else Language.HI_IN)
            return
        logger.debug(f"Whisper {len(audio) / 32000:.1f}s audio -> {secs:.2f}s [{lang}] "
                     f"lp={r['avg_logprob']:.2f} cr={r['compression_ratio']:.2f}{' UNSURE' if unsure else ''}: [{text}]")
        if text:
            self.turn_unsure = self.turn_unsure or unsure
            self.turn_heard.append({"text": text, "unsure": unsure})
            language = Language.EN_IN if lang == "en" else Language.HI_IN
            await self._handle_transcription(text, True, language)
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), language)


def make_whisper_stt(call_id: str | None = None, record: bool = False, name: str | None = None):
    return WhisperHiEnSTTService(call_id=call_id, record=record, prompt=call_prompt(name))
