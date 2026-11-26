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
    {"key": "stt_provider", "group": "Speech to text", "type": "select", "default": "sarvam",
     "env": "STT_PROVIDER", "label": "STT engine (switch Sarvam / AI4Bharat)",
     "options": [["sarvam", "Sarvam (cloud, streaming)"], ["indic", "AI4Bharat IndicConformer 600M (local)"]],
     "help": "Applies from the next call. IndicConformer is open source and runs on this machine; it is not "
             "streaming, so turn detection switches to Local Silero. If it isn't ready, calls fall back to Sarvam."},
    {"key": "indic_decoding", "group": "Speech to text", "type": "select", "default": "ctc",
     "env": "INDIC_DECODING", "label": "IndicConformer decoding",
     "options": [["ctc", "CTC (faster)"], ["rnnt", "RNNT (often more accurate)"]],
     "help": "IndicConformer only."},
    {"key": "indic_language", "group": "Speech to text", "type": "select", "default": "hi",
     "env": "INDIC_LANGUAGE", "label": "IndicConformer language",
     "options": [["hi", "Hindi"], ["mr", "Marathi"]],
     "help": "IndicConformer only. The model has no English; English words come back in Devanagari."},
    {"key": "stt_model", "group": "Speech to text", "type": "select", "default": "saaras:v4",
     "env": "SARVAM_STT_MODEL", "label": "STT model",
     "options": [["saaras:v4", "saaras:v4"], ["saaras:v3-realtime", "saaras:v3-realtime"]]},
    {"key": "stt_stream_type", "group": "Speech to text", "type": "select", "default": "fast",
     "env": "SARVAM_STT_STREAM", "label": "Stream type",
     "options": [["fast", "fast"], ["balanced", "balanced"], ["simulated", "simulated"]],
     "help": "fast gives the lowest latency; balanced is a little slower and more accurate."},

    # ---- Interruptions: the usual cause of the bot going quiet mid-sentence
    {"key": "barge_in", "group": "Interruptions", "type": "bool", "default": True,
     "env": "BARGE_IN", "label": "Customer can interrupt the bot",
     "help": "When on, any speech the turn detector hears while the bot talks cuts the bot off. "
             "Line noise, echo or a cough can do that, so the bot stops mid-sentence. Turn this off to "
             "make the bot always finish its sentence. The customer is still heard afterwards."},
    {"key": "endpointing", "group": "Interruptions", "type": "select", "default": "sarvam",
     "env": "TURN_DETECTION", "label": "Turn detection",
     "options": [["sarvam", "Sarvam server VAD"], ["local", "Local Silero VAD + Smart Turn"]],
     "help": "Who decides when the customer starts and stops talking. Sarvam uses the STT settings "
             "below; Local uses the Silero settings below."},
    {"key": "stt_threshold", "group": "Interruptions", "type": "float", "default": None, "nullable": True,
     "min": 0, "max": 1, "step": 0.05, "env": "SARVAM_VAD_THRESHOLD", "label": "Sarvam VAD threshold",
     "help": "Speech probability needed to count as talking. Raise it (e.g. 0.6-0.8) if noise or echo "
             "interrupts the bot. Blank uses Sarvam's default. Sarvam turn detection only."},
    {"key": "stt_min_speech_ms", "group": "Interruptions", "type": "int", "default": None, "nullable": True,
     "min": 0, "max": 3000, "step": 50, "env": "SARVAM_MIN_SPEECH_MS", "label": "Sarvam min speech (ms)",
     "help": "Speech shorter than this is ignored. 300-500 ms filters clicks and 'hmm's. "
             "Sarvam turn detection only."},
    {"key": "stt_silence_ms", "group": "Interruptions", "type": "int", "default": None, "nullable": True,
     "min": 100, "max": 5000, "step": 50, "env": "SARVAM_SILENCE_MS", "label": "Sarvam end-of-speech silence (ms)",
     "help": "How long the customer must pause before their turn ends. Higher waits for slow talkers "
             "but adds latency. Sarvam turn detection only."},
    {"key": "vad_confidence", "group": "Interruptions", "type": "float", "default": 0.7,
     "min": 0.1, "max": 1, "step": 0.05, "env": "VAD_CONFIDENCE", "label": "Silero confidence",
     "help": "Local turn detection only. Higher ignores more noise."},
    {"key": "vad_start_secs", "group": "Interruptions", "type": "float", "default": 0.2,
     "min": 0.05, "max": 2, "step": 0.05, "env": "VAD_START_SECS", "label": "Silero start (s)",
     "help": "Local turn detection only. Speech must last this long to count. 0.4-0.6 s stops brief "
             "noises from interrupting."},
    {"key": "vad_stop_secs", "group": "Interruptions", "type": "float", "default": 0.2,
     "min": 0.05, "max": 2, "step": 0.05, "env": "VAD_STOP_SECS", "label": "Silero stop (s)",
     "help": "Local turn detection only. Silence needed before speech counts as stopped."},
    {"key": "vad_min_volume", "group": "Interruptions", "type": "float", "default": 0.6,
     "min": 0, "max": 1, "step": 0.05, "env": "VAD_MIN_VOLUME", "label": "Silero min volume",
     "help": "Local turn detection only. Quieter audio is never speech. Raise it for loud lines."},
    {"key": "rnnoise", "group": "Interruptions", "type": "bool", "default": True,
     "env": "RNNOISE", "label": "RNNoise on phone audio",
     "help": "Noise suppression on the Exotel input. Phone calls only; ignored where RNNoise isn't installed."},

    # ---- Bot voice
    {"key": "tts_source", "group": "Bot voice", "type": "select", "default": "clone",
     "env": "SARVAM_TTS_SOURCE", "label": "Voice source (switch clone / Bulbul)",
     "options": [["clone", "Cloned voice (picked in the list above)"], ["bulbul", "Sarvam Bulbul stock speaker"]],
     "help": "Applies from the next call. Bulbul keeps your picked clone, so switching back is one click. "
             "With no clone picked, calls use Bulbul either way. On Bulbul the bot introduces itself without a name."},
    {"key": "tts_model", "group": "Bot voice", "type": "select", "default": "bulbul:v4-flash",
     "env": "SARVAM_TTS_MODEL", "label": "Bulbul model",
     "options": [["bulbul:v4-flash", "bulbul:v4-flash"], ["bulbul:v3", "bulbul:v3"]],
     "help": "Bulbul voice source only."},
    {"key": "tts_voice", "group": "Bot voice", "type": "text", "default": None, "nullable": True,
     "env": "SARVAM_TTS_VOICE", "label": "Bulbul speaker",
     "suggest": ["aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit", "dev",
                 "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa", "kabir", "aayan", "shubh",
                 "ashutosh", "advait", "amelia", "sophia"],
     "help": "Bulbul voice source only. Blank uses the model's default speaker. The suggestions are bulbul:v3 "
             "speakers; bulbul:v4-flash has its own names (see Sarvam's docs). The prompt is written for a "
             "male caller, so pick a male speaker."},
    {"key": "tts_pace", "group": "Bot voice", "type": "float", "default": None, "nullable": True,
     "min": 0.5, "max": 2, "step": 0.05, "env": "SARVAM_TTS_PACE", "label": "Pace",
     "help": "0.5-2.0. Clones copy the sample's pace; about 1.1-1.2 keeps calls snappy."},
    {"key": "tts_pitch", "group": "Bot voice", "type": "float", "default": None, "nullable": True,
     "min": -0.75, "max": 0.75, "step": 0.05, "env": "SARVAM_TTS_PITCH", "label": "Pitch",
     "help": "bulbul:v4-flash stock voice only."},
    {"key": "tts_loudness", "group": "Bot voice", "type": "float", "default": None, "nullable": True,
     "min": 0.3, "max": 3, "step": 0.1, "env": "SARVAM_TTS_LOUDNESS", "label": "Loudness",
     "help": "bulbul:v4-flash stock voice only."},
    {"key": "tts_min_buffer", "group": "Bot voice", "type": "int", "default": 150,
     "min": 30, "max": 200, "step": 10, "env": "SARVAM_TTS_MIN_BUFFER", "label": "Min buffer (chars)",
     "help": "Text Sarvam collects before it starts speaking. Higher means fewer, longer audio chunks and "
             "fewer pauses inside a sentence, but a slightly later start."},
    {"key": "tts_max_chunk", "group": "Bot voice", "type": "int", "default": 300,
     "min": 50, "max": 500, "step": 10, "env": "SARVAM_TTS_MAX_CHUNK", "label": "Max chunk (chars)",
     "help": "Longest piece Sarvam synthesises at once. Raise it if long sentences break up."},

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
