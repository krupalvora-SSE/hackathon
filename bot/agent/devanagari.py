"""Rewrite Latin-script words into Devanagari for the open-source Hindi voice (Piper).

Piper's Hindi voices only pronounce Devanagari: "slot" came out as "लत" and "SolarSquare" was
unrecognisable, while the same lines written as स्लॉट / सोलरस्क्वेयर were read perfectly. Every bot
line goes through this before Piper speaks it.

Known words (English nouns in the flow lines, common Hinglish) use WORDS. Anything else, mostly
customer names, is transliterated phonetically: "Sharma" -> शर्मा, "Krupal" -> क्रुपल.
"""
from __future__ import annotations

import re
from datetime import date

from indic_transliteration import sanscript

WORDS = {
    # English words in flows/*/prompts/hi.yaml, policies.yaml and the bot's fixed lines
    "a-type": "ए-टाइप", "arrange": "अरेंज", "available": "अवेलेबल", "call": "कॉल", "callback": "कॉलबैक",
    "calls": "कॉल्स", "cleaning": "क्लीनिंग", "confirm": "कन्फ़र्म", "connect": "कनेक्ट",
    "connection": "कनेक्शन", "consultant": "कंसल्टेंट", "date": "डेट", "details": "डिटेल्स", "due": "ड्यू",
    "exact": "एग्ज़ैक्ट", "guarantee": "गारंटी", "hello": "हॅलो", "hero": "हीरो", "hold": "होल्ड",
    "ladder": "लैडर", "light": "लाइट", "maintenance": "मेंटेनेंस", "member": "मेंबर", "minute": "मिनट",
    "note": "नोट", "number": "नंबर", "ok": "ओके", "okay": "ओके", "panel": "पैनल", "panels": "पैनल्स", "plant": "प्लांट",
    "quality": "क्वालिटी", "ready": "रेडी", "record": "रिकॉर्ड", "senior": "सीनियर", "service": "सर्विस",
    "site": "साइट", "slot": "स्लॉट", "slots": "स्लॉट्स", "sms": "एसएमएस", "solar": "सोलर",
    "solarsquare": "सोलरस्क्वेयर", "source": "सोर्स", "system": "सिस्टम", "team": "टीम", "time": "टाइम",
    "visit": "विज़िट", "water": "वॉटर",
    # English words in main's Hinglish prompts (SSE-15541..15557) the phonetic fallback mangled
    # ("technician" -> तेच्निचिअन, "sorry" -> सोर्र्य)
    "ai": "एआई", "assistant": "असिस्टेंट", "availability": "अवेलेबिलिटी", "book": "बुक", "check-up": "चेक-अप",
    "don't": "डोंट", "english": "इंग्लिश", "fix": "फ़िक्स", "i": "आई", "i'm": "आयम", "language": "लैंग्वेज",
    "line": "लाइन", "plan": "प्लान", "problem": "प्रॉब्लम", "records": "रिकॉर्ड्स", "reminder": "रिमाइंडर",
    "request": "रिक्वेस्ट", "sorry": "सॉरी", "technician": "टेक्नीशियन", "this": "दिस", "understand": "अंडरस्टैंड",
    "visits": "विज़िट्स", "voice": "वॉइस",
    # common English in Hinglish calls (voice_training/script_hinglish.txt uses these)
    "booking": "बुकिंग", "booked": "बुक्ड", "cancel": "कैंसल", "reschedule": "रीशेड्यूल", "schedule": "शेड्यूल",
    "please": "प्लीज़", "thank": "थैंक", "thanks": "थैंक्स", "you": "यू", "actually": "एक्चुअली",
    "morning": "मॉर्निंग", "evening": "ईवनिंग", "afternoon": "आफ़्टरनून", "weekend": "वीकेंड",
    "next": "नेक्स्ट", "week": "वीक", "month": "मंथ", "free": "फ़्री", "busy": "बिज़ी", "office": "ऑफ़िस",
    "address": "एड्रेस", "location": "लोकेशन", "roof": "रूफ़", "terrace": "टैरेस", "society": "सोसाइटी",
    "building": "बिल्डिंग", "flat": "फ़्लैट", "watchman": "वॉचमैन", "key": "की", "access": "एक्सेस",
    "power": "पावर", "electricity": "इलेक्ट्रिसिटी", "meter": "मीटर", "inverter": "इन्वर्टर",
    "generation": "जनरेशन", "units": "यूनिट्स", "dust": "डस्ट", "clean": "क्लीन", "wash": "वॉश",
    "charge": "चार्ज", "charges": "चार्जेस", "payment": "पेमेंट",
    "amc": "एएमसी", "contract": "कॉन्ट्रैक्ट", "warranty": "वारंटी", "customer": "कस्टमर", "care": "केयर",
    "support": "सपोर्ट", "manager": "मैनेजर", "engineer": "इंजीनियर", "message": "मैसेज", "whatsapp": "व्हाट्सऐप",
    "update": "अपडेट", "status": "स्टेटस", "process": "प्रोसेस", "minutes": "मिनट्स", "hour": "आवर",
    "hours": "आवर्स", "done": "डन", "sure": "श्योर", "perfect": "परफ़ेक्ट", "great": "ग्रेट", "good": "गुड",
    "right": "राइट", "correct": "करेक्ट", "wrong": "रॉन्ग", "same": "सेम", "day": "डे", "today": "टुडे",
    "tomorrow": "टुमॉरो", "rain": "रेन", "weather": "वेदर", "emergency": "इमरजेंसी", "hospital": "हॉस्पिटल",
    "options": "ऑप्शंस", "option": "ऑप्शन", "machine": "मशीन", "price": "प्राइस", "folding": "फ़ोल्डिंग",
    "meeting": "मीटिंग", "robot": "रोबोट", "extra": "एक्स्ट्रा", "regular": "रेगुलर", "of": "ऑफ़",
    "photo": "फ़ोटो", "registered": "रजिस्टर्ड", "complaint": "कंप्लेंट", "weekday": "वीकडे", "bill": "बिल",
    "lift": "लिफ़्ट", "battery": "बैटरी", "check": "चेक", "trained": "ट्रेंड", "pet": "पेट", "safety": "सेफ़्टी",
    "area": "एरिया", "tension": "टेंशन",
    "out": "आउट", "station": "स्टेशन", "travel": "ट्रैवल", "family": "फ़ैमिली", "function": "फ़ंक्शन",
    # persona, and common names/surnames the phonetic fallback gets wrong (Patel -> पतेल, Rahul -> रहुल)
    "krupal": "कृपाल", "jasir": "जासिर", "rahul": "राहुल", "patel": "पटेल", "shah": "शाह", "mehta": "मेहता",
    "ramesh": "रमेश", "rajesh": "राजेश", "vikas": "विकास", "sunita": "सुनीता", "anjali": "अंजलि", "vikram": "विक्रम",
    "manoj": "मनोज", "arjun": "अर्जुन", "sanjay": "संजय", "geeta": "गीता", "amit": "अमित", "deepak": "दीपक",
    "priya": "प्रिया", "meena": "मीना", "nitin": "नितिन", "asha": "आशा", "neha": "नेहा", "suresh": "सुरेश",
    "kavita": "कविता", "pooja": "पूजा", "sneha": "स्नेहा", "rekha": "रेखा",
    "gupta": "गुप्ता", "verma": "वर्मा", "singh": "सिंह", "kumar": "कुमार", "joshi": "जोशी", "desai": "देसाई",
    # filler lines and words the LLM uses in replies (2026-11-27 call: "जी, एक second" was read "सेचोन्द")
    "second": "सेकंड", "seconds": "सेकंड्स", "moment": "मोमेंट", "nice": "नाइस", "wife": "वाइफ़",
    "personal": "पर्सनल", "accident": "एक्सीडेंट", "phone": "फ़ोन", "tank": "टैंक", "pipe": "पाइप",
    "bucket": "बकेट", "mop": "मॉप",
    # dates (speak_date says "Shanivar, 29 November"; the fallback made these शनिवर, नोवेम्बेर, देचेम्बेर)
    "somvar": "सोमवार", "somwar": "सोमवार", "mangalvar": "मंगलवार", "mangalwar": "मंगलवार",
    "budhvar": "बुधवार", "budhwar": "बुधवार", "guruvar": "गुरुवार", "guruwar": "गुरुवार",
    "shukravar": "शुक्रवार", "shukrawar": "शुक्रवार", "shanivar": "शनिवार", "shaniwar": "शनिवार",
    "ravivar": "रविवार", "raviwar": "रविवार", "itvaar": "इतवार", "itwar": "इतवार",
    "monday": "मंडे", "tuesday": "ट्यूज़डे", "wednesday": "वेडनेसडे", "thursday": "थर्सडे", "friday": "फ़्राइडे",
    "saturday": "सैटरडे", "sunday": "संडे",
    "january": "जनवरी", "jan": "जनवरी", "february": "फ़रवरी", "feb": "फ़रवरी", "march": "मार्च",
    "april": "अप्रैल", "apr": "अप्रैल", "may": "मई", "june": "जून", "jun": "जून", "july": "जुलाई", "jul": "जुलाई",
    "august": "अगस्त", "aug": "अगस्त", "september": "सितंबर", "sep": "सितंबर", "sept": "सितंबर",
    "october": "अक्टूबर", "oct": "अक्टूबर", "november": "नवंबर", "nov": "नवंबर",
    "december": "दिसंबर", "dec": "दिसंबर", "tarikh": "तारीख़", "tareekh": "तारीख़", "baje": "बजे",
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


_HI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.I)


def _spoken_date(day: str, month: str, whole: str) -> str:
    d, m = int(day), int(month)
    return f"{d} {_HI_MONTHS[m - 1]}" if 1 <= d <= 31 and 1 <= m <= 12 else whole


def _dates(text: str) -> str:
    """Dates the way they are said: 2026-11-29 / 29/11 -> "29 नवंबर", 29th -> 29 (else read digit by digit)."""
    text = _ISO_DATE.sub(lambda m: _spoken_date(m[3], m[2], m[0]), text)
    text = _DMY_DATE.sub(lambda m: _spoken_date(m[1], m[2], m[0]), text)
    return _ORDINAL.sub(r"\1", text)


_DAY_MONTH = re.compile(rf"\b(\d{{1,2}}) ({'|'.join(_HI_MONTHS)})(?![\u0900-\u097F])")
NEAR_DAYS = 14


def _near_day(m: re.Match, today: date) -> str:
    """Within the next two weeks the weekday + day number is enough: "सोमवार, 30 नवंबर" -> "सोमवार, 30".
    The trained Hindi voices garble month names they saw in only a few lines (नवंबर: 5 of 231)."""
    day, month = int(m[1]), _HI_MONTHS.index(m[2]) + 1
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return m[0]
        if 0 <= (d - today).days <= NEAR_DAYS:
            return m[1]
    return m[0]


# Spoken-only respellings for the trained Krupal voice (hi_IN-krupal-medium, retrained 2026-11-27), picked by
# speaking each candidate and checking what local Whisper hears. The standard spellings came out as:
# सोलरस्क्वेयर -> "सुलार सुए" (heard as "soluable"), सोलर -> "सुलर", नवंबर -> "नॉम्बर", जनवरी -> "जहनेवारी".
# Applied only on the voice path, never to training transcripts (near_dates=False).
VOICE_SPELLINGS = {"सोलरस्क्वेयर": "सौलर स्क्वैर", "सोलर": "सौलर", "नवंबर": "नोवेंबर", "जनवरी": "जैनुअरी"}
_VOICE_SPELLING = re.compile(rf"(?<![ऀ-ॿ])({'|'.join(VOICE_SPELLINGS)})(?![ऀ-ॿ])")


def to_devanagari(text: str, today: date | None = None, *, near_dates: bool = True) -> str:
    """near_dates=False keeps every month and the standard spellings (training transcripts must match what
    was read aloud); the default is the voice path."""
    from .normalize import today_ist

    text = _dates(text)
    for pat, rep in _PHRASES:
        text = pat.sub(rep, text)
    text = _LATIN.sub(_word, text)
    if not near_dates:
        return text
    today = today or today_ist()
    text = _DAY_MONTH.sub(lambda m: _near_day(m, today), text)
    from .tts_normalizer import NORMALIZER

    return NORMALIZER.normalize(_VOICE_SPELLING.sub(lambda m: VOICE_SPELLINGS[m[1]], text))


async def devanagari_transform(text: str, _text_type=None) -> str:
    return text if is_english(text) else to_devanagari(text)


DEVANAGARI_TEXT_TRANSFORMS = [("*", devanagari_transform)]
