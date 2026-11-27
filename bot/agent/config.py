"""Tunable call settings, editable on the /voice page.

Each value resolves as: saved on the page (bot/config.json, git-ignored) > .env > default.
Every call reads the config fresh, so a change applies from the next call without a restart.
A field whose value is None means "leave it to the provider's own default".
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

# type: bool | int | float | select | text. `nullable` fields can be left blank (provider default).
FIELDS: list[dict[str, Any]] = [
    # ---- Speech to text
    {"key": "stt_provider", "group": "Speech to text", "type": "select", "default": "whisper",
     "env": "STT_PROVIDER", "label": "STT engine",
     "options": [["whisper", "Whisper large-v3-turbo (local, Apple GPU)"]],
     "help": "Runs on this machine from the model already on disk (no account, no download). It doesn't "
             "stream: the Silero settings below decide when the customer has finished, then the whole "
             "reply is transcribed (about 1 s)."},

    {"key": "record_stt_audio", "group": "Speech to text", "type": "bool", "default": True,
     "env": "RECORD_STT_AUDIO", "label": "Record what speech recognition hears",
     "help": "Saves each customer segment and Whisper's transcript + confidence to bot/recordings/<call>/ "
             "(git-ignored, stays on this machine). For diagnosing mishearing and building a test set from "
             "real calls; turn off when you don't need it."},

    {"key": "record_call", "group": "Speech to text", "type": "bool", "default": True,
     "env": "RECORD_CALL", "label": "Record the whole call (both sides)",
     "help": "Saves the conversation as one stereo WAV, customer on the left and bot on the right, to "
             "bot/recordings/<call>/call.wav (git-ignored, stays on this machine). The dashboard plays it "
             "after the call. The bot already tells the customer the call is recorded."},

    # ---- Interruptions: the usual cause of the bot going quiet mid-sentence
    {"key": "barge_in", "group": "Interruptions", "type": "bool", "default": True,
     "env": "BARGE_IN", "label": "Customer can interrupt the bot",
     "help": "When on, any speech the turn detector hears while the bot talks cuts the bot off. "
             "Line noise, echo or a cough can do that, so the bot stops mid-sentence. Turn this off to "
             "make the bot always finish its sentence. The customer is still heard afterwards."},
    {"key": "barge_in_secs", "group": "Interruptions", "type": "float", "default": 0.6,
     "min": 0, "max": 2, "step": 0.1, "env": "BARGE_IN_SECS", "label": "Talk over the bot for (s) to interrupt",
     "help": "While the bot talks, the customer must keep speaking this long before it stops, so echo and "
             "coughs don't cut it off. When the bot is quiet, even a short \"हाँ\" is heard at once (that is "
             "Silero start below: keep it at 0.2-0.3 s, higher misses हाँ / जी / नहीं)."},
    {"key": "vad_confidence", "group": "Interruptions", "type": "float", "default": 0.7,
     "min": 0.1, "max": 1, "step": 0.05, "env": "VAD_CONFIDENCE", "label": "Silero confidence",
     "help": "Higher ignores more noise."},
    {"key": "vad_start_secs", "group": "Interruptions", "type": "float", "default": 0.2,
     "min": 0.05, "max": 2, "step": 0.05, "env": "VAD_START_SECS", "label": "Silero start (s)",
     "help": "Speech must last this long to count. Above 0.3 s short answers (हाँ, जी, नहीं) are "
             "never heard; to make the bot harder to interrupt raise the talk-over time above instead."},
    {"key": "vad_stop_secs", "group": "Interruptions", "type": "float", "default": 0.2,
     "min": 0.05, "max": 2, "step": 0.05, "env": "VAD_STOP_SECS", "label": "Silero stop (s)",
     "help": "Silence needed before speech counts as stopped."},
    {"key": "vad_min_volume", "group": "Interruptions", "type": "float", "default": 0.6,
     "min": 0, "max": 1, "step": 0.05, "env": "VAD_MIN_VOLUME", "label": "Silero min volume",
     "help": "Quieter audio is never speech. Raise it for loud lines."},

    # ---- Bot voice
    {"key": "tts_provider", "group": "Bot voice", "type": "select", "default": "piper",
     "env": "TTS_PROVIDER", "label": "TTS engine (switch Piper / Piper + my voice)",
     "options": [["piper", "Piper (open source, runs on this machine's CPU)"],
                 ["piper_clone", "Piper + my voice (open source, local clone of the sample below)"]],
     "help": "Applies from the next call. Piper only reads Devanagari, so every English or Roman-script word "
             "is rewritten in Devanagari before it is spoken (agent/devanagari.py). \"Piper + my voice\" "
             "re-voices Piper as the local sample (\"Local open-source voice\" above) through the converter "
             "in bot/clone_tts; if the converter isn't running, calls speak plain Piper."},
    {"key": "piper_voice", "group": "Bot voice", "type": "select", "default": "hi_IN-jasir-medium",
     "env": "PIPER_VOICE", "label": "Piper speaker",
     "options": [["hi_IN-pratham-medium", "Pratham (male, Hindi)"], ["hi_IN-rohan-medium", "Rohan (male, Hindi)"],
                 ["hi_IN-jasir-medium", "Jasir (male, Hindi, trained on our recordings)"],
                 ["hi_IN-krupal-medium", "Krupal (male, Hinglish, trained on our recordings)"]],
     "help": "Male Hindi voices; the bot is a male telecaller. Jasir is our own fine-tuned voice "
             "(bot/voice_training). Voices load only from ~/.cache/pipecat/piper/ (the .onnx and .onnx.json "
             "files); nothing is downloaded, so copy a voice there before picking it."},
    {"key": "piper_voice_en", "group": "Bot voice", "type": "select", "default": "en_US-ryan-medium",
     "env": "PIPER_VOICE_EN", "label": "Piper English speaker",
     "options": [["en_US-ryan-medium", "Ryan (male, US English)"], ["en_GB-alan-medium", "Alan (male, UK English)"]],
     "help": "Speaks the English lines when the customer switches to English (the Hindi voices read "
             "English with a Hindi accent). Male; loaded from ~/.cache/pipecat/piper/ like the Hindi voice."},
    {"key": "clone_tau", "group": "Bot voice", "type": "float", "default": 0.3,
     "min": 0.1, "max": 1, "step": 0.05, "env": "CLONE_TAU", "label": "My-voice conversion strength (tau)",
     "help": "Piper + my voice only. OpenVoice's tau: lower sticks closer to your sample's voice, higher keeps "
             "more of Piper's clarity. 0.3 is OpenVoice's default; try 0.2-0.5."},
    {"key": "piper_noise_scale", "group": "Bot voice", "type": "float", "default": 0.33,
     "min": 0, "max": 1, "step": 0.01, "env": "PIPER_NOISE_SCALE", "label": "Voice variation (noise scale)",
     "help": "How much Piper varies the sound. Lower is steadier and clearer; Piper's default is 0.667. "
             "Our Jasir voice (trained on 8 min of audio) slurs words at 0.667: measured Whisper character "
             "error 34% at 0.667/0.8 vs 21% at 0.33/0.5, with the worst line 75% -> 32%."},
    {"key": "piper_noise_w", "group": "Bot voice", "type": "float", "default": 0.5,
     "min": 0, "max": 1, "step": 0.01, "env": "PIPER_NOISE_W", "label": "Rhythm variation (noise w)",
     "help": "How much Piper varies the length of sounds. Lower is steadier; Piper's default is 0.8."},
    {"key": "tts_pace", "group": "Bot voice", "type": "float", "default": None, "nullable": True,
     "min": 0.5, "max": 2, "step": 0.05, "env": "TTS_PACE", "label": "Pace",
     "help": "0.5-2.0. Blank is the voice's natural speed; about 1.1-1.2 keeps calls snappy."},

    # ---- LLM
    {"key": "llm_provider", "group": "LLM", "type": "select", "default": "google",
     "env": "LLM_PROVIDER", "label": "Provider",
     "options": [["google", "Google Gemini"], ["openai", "OpenAI-compatible"]],
     "help": "OpenAI-compatible needs LLM_API_KEY (and LLM_BASE_URL) in bot/.env."},
    {"key": "llm_model", "group": "LLM", "type": "text", "default": None, "nullable": True,
     "env": "LLM_MODEL", "label": "Model",
     "help": "Blank uses gemini-3.6-flash for Google, gpt-4.1-mini for OpenAI-compatible."},
    {"key": "llm_temperature", "group": "LLM", "type": "float", "default": 0.3,
     "min": 0, "max": 1.5, "step": 0.05, "env": "LLM_TEMPERATURE", "label": "Temperature"},
    {"key": "llm_max_tokens", "group": "LLM", "type": "int", "default": 400,
     "min": 50, "max": 2000, "step": 10, "env": "LLM_MAX_TOKENS", "label": "Max output tokens",
     "help": "Too low cuts a reply off mid-sentence: Hindi uses several tokens per word, and Gemini "
             "counts thinking tokens too."},

    # ---- Call flow
    {"key": "silence_secs", "group": "Call flow", "type": "float", "default": 6,
     "min": 2, "max": 30, "step": 0.5, "env": "SILENCE_SECS", "label": "Silence before reprompt (s)",
     "help": "Quiet time before the bot reprompts. Three silences in a row end the call as no_answer."},
    {"key": "turn_stop_timeout", "group": "Call flow", "type": "float", "default": 5,
     "min": 1, "max": 20, "step": 0.5, "env": "USER_TURN_STOP_TIMEOUT", "label": "Turn stop fallback (s)",
     "help": "Closes a customer turn that never got an end-of-speech signal."},
    {"key": "filler", "group": "Call flow", "type": "bool", "default": True,
     "env": "FILLER", "label": "Spoken filler when the reply is slow",
     "help": "Plays a short 'जी, एक second' if the bot hasn't started its reply yet, so a long silence "
             "doesn't sound like a dropped line."},
    {"key": "filler_delay_ms", "group": "Call flow", "type": "int", "default": 1000,
     "min": 300, "max": 3000, "step": 100, "env": "FILLER_DELAY_MS", "label": "Filler delay (ms)",
     "help": "How long after the customer stops talking the bot waits before the filler. Lower sounds "
             "busier; the filler itself takes about 0.3 s to start."},
    {"key": "idle_timeout", "group": "Call flow", "type": "int", "default": 120,
     "min": 30, "max": 600, "step": 10, "env": "PIPELINE_IDLE_SECS", "label": "Hang up after idle (s)",
     "help": "Ends the call when no audio or activity flows in the pipeline for this long."},
]
BY_KEY = {f["key"]: f for f in FIELDS}


class ConfigError(ValueError):
    pass


def _coerce(field: dict, raw: Any) -> Any:
    """Turn a raw value (JSON or .env string) into the field's type, or raise ConfigError."""
    t, label = field["type"], field["label"]
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if field.get("nullable"):
            return None
        raise ConfigError(f"{label} is required")
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"{label} must be on or off")
    if t in ("int", "float"):
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{label} must be a number") from None
        if t == "int":
            if v != int(v):
                raise ConfigError(f"{label} must be a whole number")
            v = int(v)
        if "min" in field and v < field["min"] or "max" in field and v > field["max"]:
            raise ConfigError(f"{label} must be between {field['min']} and {field['max']}")
        return v
    if t == "select":
        allowed = [o[0] for o in field["options"]]
        if raw not in allowed:
            raise ConfigError(f"{label} must be one of {', '.join(allowed)}")
        return raw
    return str(raw).strip()[:200]


def _saved() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve() -> tuple[dict, dict]:
    """(values, sources) with each source one of 'page', 'env', 'default'.
    A bad .env or file value falls back to the next source instead of breaking calls."""
    saved = _saved()
    values, sources = {}, {}
    for f in FIELDS:
        k = f["key"]
        candidates = []
        if k in saved:
            candidates.append(("page", saved[k]))
        env = os.getenv(f["env"]) if f.get("env") else None
        if env is not None and env.strip() != "":
            candidates.append(("env", env))
        values[k], sources[k] = f["default"], "default"
        for src, raw in candidates:
            try:
                values[k], sources[k] = _coerce(f, raw), src
                break
            except ConfigError:
                continue
    return values, sources


def load() -> dict:
    return resolve()[0]


def save(updates: dict) -> None:
    """Validate and merge updates into config.json. A key mapped to null-reset removes it."""
    saved = _saved()
    errors = []
    for k, raw in updates.items():
        f = BY_KEY.get(k)
        if not f:
            errors.append(f"unknown setting {k}")
            continue
        try:
            saved[k] = _coerce(f, raw)
        except ConfigError as e:
            errors.append(str(e))
    if errors:
        raise ConfigError("; ".join(errors))
    CONFIG_FILE.write_text(json.dumps(saved, indent=2))


def reset(keys: list[str] | None = None) -> None:
    """Drop page overrides (all of them when keys is None), falling back to .env / defaults."""
    if keys is None:
        CONFIG_FILE.unlink(missing_ok=True)
        return
    saved = _saved()
    for k in keys:
        saved.pop(k, None)
    CONFIG_FILE.write_text(json.dumps(saved, indent=2))


def public_schema() -> list[dict]:
    return [{k: v for k, v in f.items()} for f in FIELDS]
