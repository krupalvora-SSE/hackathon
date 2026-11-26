"""Hinglish / Hindi (Devanagari) / Marathi parsers for the four visit keys.

Every parser is pure and takes `today` explicitly so tests are deterministic.
The LLM is told to pass the customer's own words (or an ISO value); these
parsers are the single place that turns words into values.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> date:
    return datetime.now(IST).date()


def _clean(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[।,.!?;:'\"()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- numbers
NUMBER_WORDS = {
    "ek": 1, "do": 2, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "che": 6, "chheh": 6, "saat": 7, "sat": 7, "aath": 8, "ath": 8, "nau": 9, "no": 9,
    "das": 10, "dus": 10, "gyarah": 11, "gyara": 11, "barah": 12, "bara": 12, "terah": 13,
    "chaudah": 14, "pandrah": 15, "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19,
    "bees": 20, "ikkis": 21, "baais": 22, "teis": 23, "chaubis": 24, "pachis": 25,
    "chhabbis": 26, "sattais": 27, "atthais": 28, "untis": 29, "tees": 30, "ikattis": 31,
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8,
    "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12,
}
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _num(tok: str) -> int | None:
    tok = tok.translate(DEVANAGARI_DIGITS)
    if tok.isdigit():
        return int(tok)
    m = re.fullmatch(r"(\d{1,2})(st|nd|rd|th)", tok)
    if m:
        return int(m.group(1))
    return NUMBER_WORDS.get(tok)


# ---------------------------------------------------------------- dates
WEEKDAYS = {  # Monday = 0
    0: ["monday", "mon", "somvar", "somwar", "somvaar", "सोमवार"],
    1: ["tuesday", "tue", "mangalvar", "mangalwar", "mangal", "मंगलवार", "मंगळवार"],
    2: ["wednesday", "wed", "budhvar", "budhwar", "budh", "बुधवार"],
    3: ["thursday", "thu", "guruvar", "guruwar", "guru", "brihaspativar", "गुरुवार", "बृहस्पतिवार"],
    4: ["friday", "fri", "shukravar", "shukrawar", "shukra", "शुक्रवार"],
    5: ["saturday", "sat", "shanivar", "shaniwar", "shani", "शनिवार"],
    6: ["sunday", "sun", "ravivar", "raviwar", "itvaar", "itwar", "रविवार", "इतवार"],
}
WEEKDAY_WORD = {w: d for d, ws in WEEKDAYS.items() for w in ws}
MONTHS = {
    1: ["january", "jan", "janvari", "जनवरी", "जानेवारी"],
    2: ["february", "feb", "farvari", "फरवरी", "फेब्रुवारी"],
    3: ["march", "mar", "मार्च"],
    4: ["april", "apr", "अप्रैल", "एप्रिल"],
    5: ["may", "mai", "मई", "मे"],
    6: ["june", "jun", "जून"],
    7: ["july", "jul", "जुलाई", "जुलै"],
    8: ["august", "aug", "अगस्त", "ऑगस्ट"],
    9: ["september", "sep", "sept", "सितंबर", "सप्टेंबर"],
    10: ["october", "oct", "aktubar", "अक्टूबर", "ऑक्टोबर"],
    11: ["november", "nov", "navambar", "नवंबर", "नोव्हेंबर"],
    12: ["december", "dec", "disambar", "दिसंबर", "डिसेंबर"],
}
MONTH_WORD = {w: m for m, ws in MONTHS.items() for w in ws}
MONTH_NAME = {m: ws[0].capitalize() for m, ws in MONTHS.items()}
WEEKDAY_HINGLISH = ["Somvar", "Mangalvar", "Budhvar", "Guruvar", "Shukravar", "Shanivar", "Ravivar"]

RELATIVE = {
    0: ["aaj", "today", "आज"],
    1: ["kal", "tomorrow", "कल", "udya", "उद्या"],
    2: ["parso", "parson", "parsoun", "परसों", "parva", "परवा", "day after tomorrow"],
}
NEXT_WORDS = {"agle", "agla", "next", "अगले", "pudhchya", "पुढच्या"}


def _next_month_day(day: int, today: date) -> date | None:
    for add in (0, 1):
        y, m = today.year, today.month + add
        if m > 12:
            y, m = y + 1, 1
        try:
            d = date(y, m, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def parse_date(text: str, today: date | None = None) -> date | None:
    """Return the date the customer means, or None if we can't tell."""
    today = today or today_ist()
    t = _clean(text)
    if not t:
        return None

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:  # Indian order: dd/mm[/yy]
        y = int(m[3]) if m[3] else today.year
        y = y + 2000 if y < 100 else y
        try:
            d = date(y, int(m[2]), int(m[1]))
            return d if m[3] or d >= today else date(y + 1, d.month, d.day)
        except ValueError:
            return None

    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    tokens = t.split()

    # "5 december", "december 5", "5 tarikh", "paanch tarikh"
    for i, tok in enumerate(tokens):
        if tok in MONTH_WORD:
            month = MONTH_WORD[tok]
            neighbours = [tokens[j] for j in (i - 1, i + 1) if 0 <= j < len(tokens)]
            for n in neighbours:
                day = _num(n)
                if day and 1 <= day <= 31:
                    for y in (today.year, today.year + 1):
                        try:
                            d = date(y, month, day)
                        except ValueError:
                            return None
                        if d >= today:
                            return d
    for i, tok in enumerate(tokens):
        if tok in ("tarikh", "tareekh", "tarik", "तारीख", "तारीख़", "तारखेला", "tarkhela", "date"):
            if i > 0 and (day := _num(tokens[i - 1])) and 1 <= day <= 31:
                return _next_month_day(day, today)

    # weekdays, optionally "agle"/"next"
    for i, tok in enumerate(tokens):
        if tok in WEEKDAY_WORD:
            wd = WEEKDAY_WORD[tok]
            ahead = (wd - today.weekday()) % 7
            if ahead == 0:
                ahead = 7
            # "agle somvar" on a Thursday means the coming Monday, same as "somvar"
            return today + timedelta(days=ahead)

    for offset, words in RELATIVE.items():
        for w in words:
            if w in tokens or (" " in w and w in t):
                return today + timedelta(days=offset)

    # bare day number "28" (only if it is the whole answer)
    if len(tokens) == 1 and (day := _num(tokens[0])) and 1 <= day <= 31:
        return _next_month_day(day, today)
    return None


def speak_date(d: date) -> str:
    return f"{WEEKDAY_HINGLISH[d.weekday()]}, {d.day} {MONTH_NAME[d.month]}"


# ---------------------------------------------------------------- time slots
SLOT_IDS = ("09-12", "12-15", "15-18")
SLOT_SPEECH = {
    "09-12": "subah 9 se 12 baje ke beech",
    "12-15": "dopahar 12 se 3 baje ke beech",
    "15-18": "shaam 3 se 6 baje ke beech",
}
MORNING = {"subah", "savere", "sawere", "morning", "सुबह", "सवेरे", "सकाळी", "sakali", "am", "a m"}
AFTERNOON = {"dopahar", "dopehar", "dupahar", "afternoon", "noon", "lunch", "दोपहर", "दुपारी", "dupari"}
EVENING = {"shaam", "sham", "evening", "शाम", "संध्याकाळी", "sandhyakali", "pm", "p m"}
NIGHT = {"raat", "rat", "night", "रात", "रात्री", "ratri"}


def slot_for_hour(hour: int) -> str | None:
    if 9 <= hour < 12:
        return "09-12"
    if 12 <= hour < 15:
        return "12-15"
    if 15 <= hour < 18:
        return "15-18"
    return None


def parse_time_slot(text: str) -> tuple[str | None, str | None]:
    """Return (slot_id, None) or (None, reason). reason is 'out_of_hours' or 'unclear'."""
    t = _clean(text)
    if not t:
        return None, "unclear"
    compact = t.replace(" ", "")
    for sid in SLOT_IDS:
        if sid in compact or sid.replace("-", "to") in compact:
            return sid, None
    tokens = set(t.split())
    if tokens & NIGHT:
        return None, "out_of_hours"

    hour = None
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)?\b", t.translate(DEVANAGARI_DIGITS))
    if m:
        hour = int(m[1])
        if m[3] in ("pm", "p m") and hour < 12:
            hour += 12
        elif m[3] in ("am", "a m") and hour == 12:
            hour = 0
    else:
        for tok in t.split():
            if (n := _num(tok)) is not None and 1 <= n <= 12 and tok not in ("no",):
                hour = n
                break

    if hour is not None:
        if hour <= 12 and tokens & EVENING and hour < 12:
            hour += 12
        elif hour < 12 and tokens & AFTERNOON and hour <= 5:
            hour += 12
        elif not (tokens & MORNING) and 1 <= hour <= 8:
            hour += 12  # bare "3 baje" / "4 bje" means afternoon on a service call
        slot = slot_for_hour(hour)
        return (slot, None) if slot else (None, "out_of_hours")

    if tokens & MORNING:
        return "09-12", None
    if tokens & AFTERNOON:
        return "12-15", None
    if tokens & EVENING:
        return "15-18", None
    return None, "unclear"


# ---------------------------------------------------------------- yes / no
YES = {"haan", "han", "ha", "haa", "haanji", "hanji", "ji", "jee", "yes", "yeah", "yep", "ok", "okay",
       "theek", "thik", "bilkul", "hai", "hain", "available", "true", "sure", "ho", "aahe", "ahe",
       "हाँ", "हां", "हा", "जी", "है", "हैं", "हो", "आहे", "ठीक", "बिल्कुल", "sahi", "correct", "right"}
NO = {"nahi", "nahin", "nai", "nahi", "no", "nope", "false", "naa", "mat", "nahiye", "nako", "nahi hai",
      "नहीं", "नही", "ना", "नाही", "नको", "galat", "wrong"}


def parse_bool(text: str) -> bool | None:
    t = _clean(text)
    if not t:
        return None
    tokens = t.split()
    if any(tok in NO for tok in tokens):
        # "haan na" / "hai na" is a tag question meaning yes
        if tokens[-1] in ("na", "naa", "ना") and any(tok in YES for tok in tokens[:-1]) \
                and not any(tok in NO - {"naa", "ना"} for tok in tokens[:-1]):
            return True
        return False
    if tokens == ["na"] or tokens[0] == "na":
        return False
    if any(tok in YES for tok in tokens):
        return True
    return None
