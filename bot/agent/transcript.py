"""What was actually said on a call, Bot vs Human, for cleaning the call audio.

The dashboard's chat shows the reply text from the LLM context. The voice says more than that: "Hello?",
fillers ("अच्छा…", "जी, एक मिनट।"), yield lines, and every line after pre-TTS respelling ("सौलर स्क्वैर").
Bot lines here are taken from the TTS service itself, so they are exactly the text Piper read.

Saved next to the call's audio: bot/recordings/<call_id>/transcript.jsonl (one line per utterance) and
transcript.txt ("Bot: …" / "Human: …"), served by the bot server at /api/transcripts/<call_id>.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

from .recording import call_wav

JSONL = "transcript.jsonl"
TXT = "transcript.txt"
LABEL = {"bot": "Bot", "human": "Human"}


def transcript_dir(call_id: str) -> Path | None:
    wav = call_wav(call_id)  # same id check: no path escaping bot/recordings
    return wav.parent if wav else None


def as_text(lines: list[dict]) -> str:
    """Bot: … / Human: … one utterance per line; an interrupted bot line is marked."""
    out = []
    for e in lines:
        cut = " [interrupted]" if e.get("interrupted") else ""
        out.append(f"{LABEL.get(e['who'], e['who'])}: {e['text']}{cut}")
    return "\n".join(out) + ("\n" if out else "")


def load(call_id: str) -> list[dict] | None:
    d = transcript_dir(call_id)
    path = d / JSONL if d else None
    if not (path and path.exists()):
        return None
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save(call_id: str, lines: list[dict]) -> Path | None:
    d = transcript_dir(call_id)
    if not d:
        return None
    d.mkdir(parents=True, exist_ok=True)
    (d / JSONL).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in lines))
    (d / TXT).write_text(as_text(lines))
    return d / JSONL


class CallTranscript:
    """Collects the call's utterances in order. `bot()` is hooked to the TTS service, `human()` to the end of
    each customer turn; `save()` writes the files once the call ends."""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self.started = time.monotonic()
        self.lines: list[dict] = []

    def _add(self, who: str, text: str) -> None:
        text = " ".join((text or "").split())
        if text:
            self.lines.append({"t": round(time.monotonic() - self.started, 2), "who": who, "text": text})

    def bot(self, text: str) -> None:
        self._add("bot", text)

    def human(self, text: str) -> None:
        self._add("human", text)

    def interrupted(self) -> None:
        """The customer cut in: the last bot line may not have been played in full."""
        for e in reversed(self.lines):
            if e["who"] == "bot":
                e["interrupted"] = True
                return

    def save(self) -> Path | None:
        try:
            return save(self.call_id, self.lines)
        except Exception as e:  # noqa: BLE001 - a failed save must never break the call
            logger.warning(f"could not save call transcript: {e}")
            return None
