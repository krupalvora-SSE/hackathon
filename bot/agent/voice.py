"""Who the bot's voice is: the name it introduces itself with ("mai SolarSquare se Jasir bol raha hoon").

Voices are local Piper voices (agent/bot.py), optionally re-voiced as a recorded sample
("Piper + my voice", agent/voice_local.py).
"""
from __future__ import annotations

STOCK_PIPER_SPEAKERS = {"pratham", "rohan"}  # Piper's own Hindi voices: not a team member


def active_voice_name() -> str | None:
    """The team member the voice belongs to, or None for a stock speaker.

    - Plain Piper with our fine-tuned voice ("hi_IN-jasir-medium"): that speaker ("Jasir").
    - "Piper + my voice": the name saved with the local sample, up to " - " ("Krupal - AMC bot" -> "Krupal").
    """
    from .config import load

    cfg = load()
    if cfg.get("tts_provider") == "piper_clone":
        from .voice_local import local_voice_name
        return local_voice_name()
    speaker = (cfg.get("piper_voice") or "").split("-")[1:2]  # a voice we fine-tuned (bot/voice_training)
    return speaker[0].title() if speaker and speaker[0] not in STOCK_PIPER_SPEAKERS else None
