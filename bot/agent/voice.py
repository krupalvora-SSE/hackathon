"""Cloned voice: which Sarvam voice the bot speaks with, and a TTS service that streams it.

The active clone is `SARVAM_CLONE_VOICE_ID` in .env, else the one picked on the /voice page
(stored in bot/voice.json, git-ignored). With neither set the bot uses the stock Bulbul speaker.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from loguru import logger
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.utils.text.transforms.replacements import replace_text

# Words Sarvam pronounces badly, rewritten just before speech (fixed lines and LLM replies alike).
# "main" (I) and "mein" (in) come out stiff; callers say "mai" for both. Whole words only, so
# "maintenance" or "remaining" are untouched.
_DEV = "\u0900-\u097F"
SPOKEN_FIXES = [
    (r"\b[Mm]ain\b", "mai"),
    (r"\b[Mm]ein\b", "mai"),
    (rf"(?<![{_DEV}])(?:में|मैं)(?![{_DEV}])", "मै"),
]
_FIXES = [(re.compile(p), r) for p, r in SPOKEN_FIXES]
TTS_TEXT_TRANSFORMS = [("*", replace_text(SPOKEN_FIXES))]


def fix_spoken_text(text: str) -> str:
    for p, r in _FIXES:
        text = p.sub(r, text)
    return text


VOICE_FILE = Path(__file__).resolve().parent.parent / "voice.json"
CLONE_WS_URL = "wss://api.sarvam.ai/voices/clone/ws?send_completion_event=true"


def active_voice_id() -> str | None:
    if env := os.getenv("SARVAM_CLONE_VOICE_ID"):
        return env
    try:
        return json.loads(VOICE_FILE.read_text()).get("voice_id") or None
    except (OSError, ValueError):
        return None


def active_voice_name() -> str | None:
    """Name the bot introduces itself with: the picked voice's name, up to " - " ("Krupal - AMC bot" -> "Krupal")."""
    try:
        name = json.loads(VOICE_FILE.read_text()).get("name") or ""
    except (OSError, ValueError):
        return None
    return name.split(" - ")[0].strip() or None


def set_active_voice(voice_id: str | None, name: str | None = None) -> None:
    if voice_id:
        VOICE_FILE.write_text(json.dumps({"voice_id": voice_id, "name": name}))
    else:
        VOICE_FILE.unlink(missing_ok=True)


class SarvamCloneTTSService(SarvamTTSService):
    """Sarvam websocket TTS pointed at the Instant Voice Cloning endpoint.

    Same streaming protocol as /text-to-speech/ws (config -> text -> flush, audio/event replies),
    but the config carries `voice_id` instead of `speaker`/`model`, and audio is raw linear16 PCM.
    """

    def __init__(self, *, clone_voice_id: str, **kwargs):
        super().__init__(**kwargs)
        self._clone_voice_id = clone_voice_id
        self._websocket_url = CLONE_WS_URL

    async def _update_settings(self, delta):
        changed = await super()._update_settings(delta)
        self._websocket_url = CLONE_WS_URL  # a model change rebuilds the stock URL; keep ours
        return changed

    async def _send_config(self):
        if not self._websocket:
            raise Exception("WebSocket not connected")
        data = {
            "voice_id": self._clone_voice_id,
            "target_language_code": self._settings.language,
            "speech_sample_rate": int(self.sample_rate),
            "output_audio_codec": "linear16",
            "min_buffer_size": self._settings.min_buffer_size,
            "max_chunk_length": self._settings.max_chunk_length,
        }
        if self._settings.pace is not None:
            data["pace"] = self._settings.pace
        data = {k: v for k, v in data.items() if v is not None}
        logger.debug(f"Sarvam clone TTS config: {data}")
        await self._websocket.send(json.dumps({"type": "config", "data": data}))
