"""Last text clean-up before Piper speaks a Hindi sentence (runs after devanagari.to_devanagari).

Numbers are left as digits on purpose: Piper's espeak-ng front end already reads them as Hindi words, offline
(30 -> "तीस", "9 से 12" -> "नौ से बारह", 52 -> "बावन"), and a word-by-word mapper gets 41-99 wrong.

Word boundaries are Devanagari-aware: Python's \\b treats vowel signs (ै, ो, ...) as non-word characters, so
r"\\bठीक है\\b" never matches. Only respellings verified on the voice belong in REPLACEMENTS; "ठीक है" already
phonemizes as [ʈʰiːk hɛː] and must not become "हे" ([heː]).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

_DEV = "\u0900-\u0963\u0966-\u097F"  # Devanagari letters and signs, not the dandas । ॥
REPLACEMENTS: list[tuple[str, str]] = [
    ("सर्विस हिरो", "सर्विस हीरो"),  # short "हि" read as [hɪɾoː]; "hero" is [hiːɾoː]
]
_QUESTION_STARTERS = ("क्या", "कब", "कहाँ", "कहां", "क्यों", "कैसे", "कौन", "कितना", "कितने", "कितनी")


@dataclass
class TTSNormalizerConfig:
    replacements: list[tuple[str, str]] = field(default_factory=lambda: list(REPLACEMENTS))


class HindiHinglishTTSNormalizer:
    """Built once (module-level NORMALIZER); normalize() is pure and cheap per sentence."""

    def __init__(self, config: TTSNormalizerConfig | None = None) -> None:
        self.config = config or TTSNormalizerConfig()
        self._replacements = [(re.compile(rf"(?<![{_DEV}]){re.escape(a)}(?![{_DEV}])"), b)
                              for a, b in self.config.replacements]
        # an orphan "के" left by a broken slot template: "... 12 के?" -> "... 12?" (never "आपके", "के पास")
        self._dangling_ke = re.compile(r"\s+के(?=\s*[?.।!]*\s*$)")
        self._question = re.compile(rf"^(?:{'|'.join(_QUESTION_STARTERS)})(?![{_DEV}])")

    def normalize(self, text: str) -> str:
        out = self._normalize(text)
        if out != text.strip():
            logger.debug(f"tts normalize: {text.strip()!r} -> {out!r}")
        return out

    def _normalize(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        for pat, rep in self._replacements:
            text = pat.sub(rep, text)
        text = self._dangling_ke.sub("", text)
        last = re.split(r"(?<=[?.।!])\s+", text)[-1]   # only the last sentence: "क्या … हैं? मैं … से कृपाल।"
        if self._question.match(last) and not text.endswith("?"):  # rising contour on direct questions
            text = re.sub(r"[.।!]+$", "", text).rstrip() + "?"
        return text


NORMALIZER = HindiHinglishTTSNormalizer()
