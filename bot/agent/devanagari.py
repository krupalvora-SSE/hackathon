"""Rewrite Latin-script words into Devanagari for the open-source Hindi voice (Piper).

Piper's Hindi voices only pronounce Devanagari: "slot" came out as "लत" and "SolarSquare" was
unrecognisable, while the same lines written as स्लॉट / सोलरस्क्वेयर were read perfectly. Sarvam
handles mixed script itself, so this runs only on the Piper engine.

Known words (English nouns in the flow lines, common Hinglish) use WORDS. Anything else, mostly
customer names, is transliterated phonetically: "Sharma" -> शर्मा, "Krupal" -> क्रुपल.
"""
from __future__ import annotations

import re

from indic_transliteration import sanscript

WORDS = {
    # English words in flows/*/prompts/hi.yaml, policies.yaml and the bot's fixed lines
    "a-type": "ए-टाइप", "arrange": "अरेंज", "available": "अवेलेबल", "call": "कॉल", "callback": "कॉलबैक",
    "calls": "कॉल्स", "cleaning": "क्लीनिंग", "confirm": "कन्फ़र्म", "connect": "कनेक्ट",
    "connection": "कनेक्शन", "consultant": "कंसल्टेंट", "date": "डेट", "details": "डिटेल्स", "due": "ड्यू",
    "exact": "एग्ज़ैक्ट", "guarantee": "गारंटी", "hello": "हेलो", "hero": "हीरो", "hold": "होल्ड",
    "ladder": "लैडर", "light": "लाइट", "maintenance": "मेंटेनेंस", "member": "मेंबर", "minute": "मिनट",
    "note": "नोट", "number": "नंबर", "ok": "ओके", "okay": "ओके", "panel": "पैनल", "panels": "पैनल्स", "plant": "प्लांट",
    "quality": "क्वालिटी", "ready": "रेडी", "record": "रिकॉर्ड", "senior": "सीनियर", "service": "सर्विस",
    "site": "साइट", "slot": "स्लॉट", "slots": "स्लॉट्स", "sms": "एसएमएस", "solar": "सोलर",
    "solarsquare": "सोलरस्क्वेयर", "source": "सोर्स", "system": "सिस्टम", "team": "टीम", "time": "टाइम",
    "visit": "विज़िट", "water": "वॉटर",
    # persona, and common names/surnames the phonetic fallback gets wrong (Patel -> पतेल, Rahul -> रहुल)
    "krupal": "कृपाल", "jasir": "जासिर", "rahul": "राहुल", "patel": "पटेल", "shah": "शाह", "mehta": "मेहता",
    "gupta": "गुप्ता", "verma": "वर्मा", "singh": "सिंह", "kumar": "कुमार", "joshi": "जोशी", "desai": "देसाई",
}

# Hinglish (checklist flow lines and LLM replies in Roman script). Also tells Roman Hinglish from English.
HINGLISH = {
    "aa": "आ", "aap": "आप", "aapka": "आपका", "aapke": "आपके", "aapko": "आपको", "aawaz": "आवाज़",
    "aur": "और", "baad": "बाद", "baare": "बारे", "baat": "बात", "bahut": "बहुत", "batayenge": "बताएँगे",
    "bilkul": "बिल्कुल", "dhanyavaad": "धन्यवाद", "din": "दिन", "dobara": "दोबारा", "ek": "एक",
    "galat": "गलत", "gaya": "गया", "hai": "है", "hain": "हैं", "hamare": "हमारे", "ho": "हो",
    "hoon": "हूँ", "hum": "हम", "jayega": "जाएगा", "ji": "जी", "ka": "का", "kar": "कर", "karenge": "करेंगे",
    "ke": "के", "ki": "की", "kijiye": "कीजिए", "ko": "को", "kya": "क्या", "lag": "लग", "lagta": "लगता",
    "liye": "लिए", "maaf": "माफ़", "mai": "मैं", "main": "मैं", "mein": "में", "mujhe": "मुझे",
    "nahi": "नहीं", "namaste": "नमस्ते", "pa": "पा", "par": "पर", "pe": "पे", "pehle": "पहले",
    "raha": "रहा", "rahe": "रहे", "rahi": "रही", "sakte": "सकते", "se": "से", "shubh": "शुभ", "sun": "सुन",
    "theek": "ठीक", "woh": "वह", "yeh": "यह",
    "haan": "हाँ", "nahin": "नहीं", "acha": "अच्छा", "accha": "अच्छा", "kal": "कल", "aaj": "आज",
}
WORDS.update(HINGLISH)

# Romanized "mai" is both मैं (I) and में (in); after these words it means "in".
_PHRASES = [(re.compile(rf"\b({w})\s+mai\b", re.I), rf"\1 mein") for w in ("baad", "baare", "ghar", "din", "time")]
_LATIN = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_FINAL_VOWEL = re.compile(r"(?<=[^aeiou])(a|i|u)$")


def _transliterate(word: str) -> str:
    """Romanized Hindi word -> Devanagari. Final a/i/u are long in names (Sharma, Priya, Babu),
    and Hindi drops the final inherent vowel, so no trailing halant (Krupal -> क्रुपल, not क्रुपल्)."""
    w = _FINAL_VOWEL.sub(lambda m: m.group(1) * 2, word.lower().replace("'", "").replace("’", ""))
    return sanscript.transliterate(w, sanscript.OPTITRANS, sanscript.DEVANAGARI).rstrip("्")


def _word(m: re.Match) -> str:
    w = m.group(0)
    return WORDS.get(w.lower()) or "-".join(WORDS.get(p.lower()) or _transliterate(p) for p in w.split("-") if p)


_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def is_english(text: str) -> bool:
    """An English sentence (customer switched to English), spoken by the English Piper voice as is.
    Any Devanagari, a lone word ("Hello?", a name) or Roman Hinglish stays Hindi."""
    if _DEVANAGARI.search(text):
        return False
    words = [w.lower() for w in _LATIN.findall(text)]
    return len(words) >= 3 and sum(w in HINGLISH for w in words) * 3 < len(words)


def to_devanagari(text: str) -> str:
    for pat, rep in _PHRASES:
        text = pat.sub(rep, text)
    return _LATIN.sub(_word, text)


async def devanagari_transform(text: str, _text_type=None) -> str:
    return text if is_english(text) else to_devanagari(text)


DEVANAGARI_TEXT_TRANSFORMS = [("*", devanagari_transform)]
