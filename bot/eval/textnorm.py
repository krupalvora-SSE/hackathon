"""Script-agnostic scoring for Hinglish transcripts.

Sarvam (codemix) returns a mix of Latin and Devanagari; IndicConformer returns only Devanagari.
Plain WER would punish whichever engine uses a different script from the reference, so both sides
are reduced to a loose Latin "skeleton" first: Devanagari is transliterated, long vowels folded,
doubled letters collapsed. CER on that skeleton is fair across engines.

Entity accuracy is the headline number: did the words that drive the call (yes/no, day, date,
time band, ladder, water) survive? A wrong "haan" breaks the call; a misspelt filler doesn't.
"""
from __future__ import annotations

import re
import unicodedata

_V = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai",
      "ओ": "o", "औ": "au", "ऑ": "o", "ॲ": "e"}
_M = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
      "ौ": "au", "ॉ": "o", "ॅ": "e"}
_C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
      "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d",
      "ध": "dh", "न": "n", "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r",
      "ल": "l", "ळ": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "क़": "k", "ख़": "kh",
      "ग़": "g", "ज़": "z", "ड़": "d", "ढ़": "dh", "फ़": "f", "य़": "y"}
_SIGNS = {"ं": "n", "ँ": "n", "ः": "h", "्": "", "़": ""}
_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}


def translit(text: str) -> str:
    """Devanagari -> rough Latin. Inherent 'a' is dropped (Hindi schwa deletion makes it noise)."""
    text = unicodedata.normalize("NFC", text)
    out = []
    for ch in text:
        if ch in _C:
            out.append(_C[ch])
        elif ch in _M:
            out.append(_M[ch])
        elif ch in _V:
            out.append(_V[ch])
        elif ch in _SIGNS:
            out.append(_SIGNS[ch])
        elif ch in _DIGITS:
            out.append(_DIGITS[ch])
        else:
            out.append(ch)
    return "".join(out)


def skeleton(text: str) -> str:
    s = translit(text).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = s.replace("w", "v").replace("z", "j").replace("ph", "f").replace("ee", "i").replace("oo", "u")
    s = re.sub(r"(?<=[a-z])a(?=[a-z])", "", s)  # drop inner schwa-ish 'a's: "haan"/"han", "subah"/"subh"
    s = re.sub(r"(.)\1+", r"\1", s)            # doubled letters
    return re.sub(r"\s+", " ", s).strip()


def _edit(a: list | str, b: list | str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    r, h = skeleton(ref).replace(" ", ""), skeleton(hyp).replace(" ", "")
    return _edit(r, h) / max(1, len(r))


def wer(ref: str, hyp: str) -> float:
    r, h = skeleton(ref).split(), skeleton(hyp).split()
    return _edit(r, h) / max(1, len(r))


# Entity -> accepted spellings, matched on the skeleton so both scripts and spellings hit.
ENTITIES: dict[str, list[str]] = {
    "yes": ["haan", "han", "ha ji", "haji", "ji haan", "theek", "thik", "sahi", "chalega", "okay", "ok", "done",
            "bilkul", "हाँ", "हां", "ठीक", "चलेगा", "बिल्कुल"],
    "no": ["nahi", "nahin", "nai", "mat", "नहीं", "नही", "मत"],
    "monday": ["somvar", "monday", "सोमवार", "मंडे"],
    "tuesday": ["mangalvar", "tuesday", "मंगलवार", "ट्यूसडे"],
    "wednesday": ["budhvar", "wednesday", "बुधवार", "वेडनेसडे"],
    "thursday": ["guruvar", "thursday", "गुरुवार", "थर्सडे"],
    "friday": ["shukravar", "friday", "शुक्रवार", "फ्राइडे"],
    "saturday": ["shanivar", "saturday", "शनिवार", "सैटरडे"],
    "sunday": ["ravivar", "sunday", "रविवार", "संडे", "इतवार"],
    "tomorrow": ["kal", "कल", "tomorrow"],
    "day_after": ["parso", "परसों", "परसो"],
    "morning": ["subah", "morning", "सुबह", "मॉर्निंग"],
    "afternoon": ["dopahar", "dopehar", "afternoon", "दोपहर"],
    "evening": ["shaam", "sham", "evening", "शाम", "ईवनिंग"],
    "ladder": ["seedhi", "sidhi", "ladder", "सीढ़ी", "सीढी", "लैडर"],
    "water": ["paani", "pani", "water", "nal", "पानी", "नल", "वॉटर"],
    "son": ["beta", "bete", "बेटा", "बेटे"],
    "human": ["insaan", "insan", "manager", "kisi aur", "इंसान", "मैनेजर"],
    "busy": ["busy", "vyast", "बिज़ी", "बिजी", "व्यस्त"],
    "wrong_number": ["galat", "wrong", "गलत", "रॉंग"],
    "callback": ["baad mein", "baad me", "baadme", "call karo", "बाद में", "कॉल करो"],
}
_ENT_SK = {k: [skeleton(v) for v in vs] for k, vs in ENTITIES.items()}
_NUM_WORDS = {"ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5, "chhe": 6, "saat": 7, "aath": 8, "nau": 9,
              "das": 10, "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16,
              "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20, "ikkis": 21, "bais": 22, "teis": 23,
              "chaubis": 24, "pachchis": 25, "chhabbis": 26, "sattais": 27, "atthais": 28, "untis": 29,
              "tees": 30, "ikattis": 31}
_NUM_SK = {skeleton(k): v for k, v in _NUM_WORDS.items()}


def has_entity(text: str, entity: str) -> bool:
    """entity is a key of ENTITIES, or 'num:<n>' for a spoken or written day-of-month."""
    sk = f" {skeleton(text)} "
    if entity.startswith("num:"):
        n = int(entity[4:])
        return f" {n} " in sk or any(f" {w} " in sk for w, v in _NUM_SK.items() if v == n)
    return any(f" {v} " in sk for v in _ENT_SK[entity])


def entity_score(text: str, expected: list[str], forbidden: list[str] = ()) -> tuple[int, int, list[str]]:
    """(hits, total, misses). A forbidden entity that appears counts as a miss (e.g. 'no' heard as 'yes')."""
    misses = [e for e in expected if not has_entity(text, e)]
    misses += [f"!{e}" for e in forbidden if has_entity(text, e)]
    total = len(expected) + len(forbidden)
    return total - len(misses), total, misses
