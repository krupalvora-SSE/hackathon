"""Cloned voice: which Sarvam voice the bot speaks with, and a TTS service that streams it.

The active clone is `SARVAM_CLONE_VOICE_ID` in .env, else the one picked on the /voice page
(stored in bot/voice.json, git-ignored). With neither set, or the voice source
set to Bulbul on the /voice page, the bot uses the stock Bulbul speaker.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
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
    (r"\b[Aa]-[Tt]ype\b", "ए टाइप"),     # read as "air type" otherwise
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


def clone_in_use(cfg: dict | None = None) -> str | None:
    """Clone id the call speaks with, or None when the voice source is Bulbul (or no clone is picked)."""
    if cfg is None:
        from .config import load
        cfg = load()
    return active_voice_id() if cfg.get("tts_source", "clone") == "clone" else None


def active_voice_name() -> str | None:
    """Name the bot introduces itself with: the picked clone's name, up to " - " ("Krupal - AMC bot" -> "Krupal").
    None on the Bulbul voice source: the stock speaker isn't that person."""
    if not clone_in_use():
        return None
    try:
        name = json.loads(VOICE_FILE.read_text()).get("name") or ""
    except (OSError, ValueError):
        return None
    return name.split(" - ")[0].strip() or None


async def _account_voice_ids() -> set[str] | None:
    """Voice ids on the Sarvam account, or None if that can't be checked right now."""
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("https://api.sarvam.ai/voices", params={"limit": 200},
                            headers={"api-subscription-key": os.environ["SARVAM_API_KEY"]})
        r.raise_for_status()
        return {v["id"] for v in r.json()["data"]["voices"] if v.get("status") == "active"}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"could not check the cloned voice, assuming it exists: {e}")
        return None


async def drop_stale_clone() -> None:
    """If the picked clone is gone from the account, clear it so the call uses the stock voice
    (and the stock persona name) instead of going silent on "Voice not found"."""
    voice_id = active_voice_id()
    if not voice_id or os.getenv("SARVAM_CLONE_VOICE_ID"):  # an id pinned in .env is the user's call
        return
    ids = await _account_voice_ids()
    if ids is not None and voice_id not in ids:
        logger.warning(f"cloned voice {voice_id} no longer exists on Sarvam; using the stock voice")
        set_active_voice(None)


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
