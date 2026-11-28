"""Answer plain turns without the LLM: a bare yes / no to a yes-no question, or one offered time window.

Each LLM turn costs ~1.5 s (Gemini only classifies; the reply is a fixed flow line), so the bot paused ~2.6 s
after every answer and a filler covered most of it (CALL-6517ea26). Most answers are just "हाँ जी", "नहीं",
"हाँ जी, मैं available हूँ" or "सुबह 9 से 12": code can read those exactly and skip the LLM.

Strict on purpose: every word must be a yes / no word, a courtesy word, or a word from the question just
asked. Anything else ("नहीं, शाम को आना", "हाँ पर ...", "शायद") goes to the LLM as before. Never on a
transcript Whisper is unsure of.
"""
from __future__ import annotations

import re

from . import normalize as N
from .flow import band_from_range, parse_band

# Steps whose question is a plain yes / no. READINESS_CHECK only at its ask_<item> questions ("is there a
# ladder?"), not "by when can you arrange it?". ALTERNATE_DAYS / ASK_SLOT offer choices: "हाँ" is ambiguous there.
YES_NO_STATES = {"GREET", "OFFER_PRIMARY", "TIME_BAND", "READINESS_CHECK", "NOT_NEEDED", "RECAP", "HOLD_CONSENT"}
COMMIT_STATES = {"RECAP", "HOLD_CONSENT"}   # a yes here books / pauses the site: needs a clear yes word
BAND_STATES = {"ASK_SLOT"}

YES_WORDS = N.CLEAR_YES | {"जी", "ji", "jee", "हांजी"}
NO_WORDS = {"नहीं", "नही", "nahi", "nahin", "no", "nope", "नाही", "नो"}
# Words that only carry a yes / no ("मैं available हूँ", "मिल जाएगा", "है ना"), never a different answer.
GLUE = {"है", "हैं", "हूँ", "हूं", "हुँ", "हु", "मैं", "main", "mai", "hoon", "hu", "hai", "hain", "तो", "to",
        "का", "की", "के", "ka", "ki", "ke", "sir", "सर", "मेरे", "पास", "हम", "हमारे", "मिल", "जाएगा", "जायेगा",
        "जाएगी", "हो", "रहूँगा", "रहूंगा", "रहेंगे", "रहेगा", "रहेगी", "बिल्कुल", "एकदम", "ekdam", "available",
        "अवेलेबल", "please", "प्लीज़", "प्लीज", "बोल", "रहा", "रही"}
TIME_GLUE = {"सुबह", "सुबर", "दोपहर", "शाम", "से", "बजे", "के", "बीच", "तक", "वाला", "वाले", "का", "ठीक", "है",
             "जी", "हाँ", "हां", "बजे", "subah", "dopahar", "shaam", "se", "baje", "tak", "morning", "afternoon",
             "evening", "to", "चलेगा", "रखिए", "रख", "दीजिए", "दो"}
NEVER_ECHO = NO_WORDS | {"क्या", "या", "kya", "ya", "कब", "कौन", "कितने", "नहीं"}
TAG = {"ना", "na", "naa"}   # "है ना" / "हाँ ना": a tag question that means yes, only as the last word
_WORD = re.compile(r"[\wऀ-ॿ'-]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(N._clean(text).translate(N.DEVANAGARI_DIGITS))


def _echo(call) -> set[str]:
    """Words of the question just asked, as written and as spoken (the voice reads "ladder" as लैडर)."""
    from .devanagari import to_devanagari

    line = getattr(call, "last_line", "") or ""
    return (set(_words(line)) | set(_words(to_devanagari(line, near_dates=False)))) - NEVER_ECHO


def _number(w: str) -> bool:
    return w.isdigit() or N._num(w) is not None


def _yes_no(words: list[str], call, heard: str) -> str | None:
    allowed = YES_WORDS | NO_WORDS | GLUE | _echo(call)
    body = words[:-1] if len(words) > 1 and words[-1] in TAG else words
    if not body or any(w not in allowed for w in body):
        return None
    yes, no = any(w in YES_WORDS for w in body), any(w in NO_WORDS for w in body)
    if yes == no:                                   # neither, or both ("हाँ... नहीं")
        return None
    if no and len(body) < len(words):               # "नहीं है ना?" reads both ways
        return None
    if yes and call.state in COMMIT_STATES and not N.clear_yes(heard):
        return None
    return "affirm" if yes else "deny"


def _band(words: list[str], call, heard: str) -> str | None:
    band = band_from_range(heard, call.policies)
    if not band or band not in call._free_bands() or parse_band(heard, call.policies) != band:
        return None
    allowed = TIME_GLUE | _echo(call)
    return band if all(w in allowed or _number(w) for w in words) else None


# "क्या आप फिर से दोहराएंगे?", "सुनाई नहीं दिया", "फिर से बोलिए", "repeat please": the customer didn't hear the
# question. Every word must be one of these; at least one must be a clear cue (REPEAT_CUE, or "फिर से" + a verb).
REPEAT_WORDS = {"क्या", "आप", "फिर", "से", "दोहराएंगे", "दोहराएँगे", "दोहराइए", "दोहराओ", "दोहरा", "दोराएंगे",
                "दोबारा", "बोलिए", "बोलिये", "बोलो", "बोला", "बोले", "बताइए", "कहा", "कहिए", "सुनाई", "नहीं", "दिया",
                "दी", "समझ", "आया", "एक", "बार", "और", "जी", "sorry", "सॉरी", "please", "प्लीज़", "प्लीज", "repeat",
                "रिपीट", "again", "pardon", "what", "kya", "phir", "se", "dobara", "boliye", "bolo", "suna", "sunai",
                "nahi", "diya", "आवाज़", "आवाज", "आई", "आ", "रही", "हैलो", "hello", "करिए", "कीजिए", "can", "you", "say",
                "that", "it", "आपने", "ज़रा", "जरा"}
REPEAT_CUE = {"दोहराएंगे", "दोहराएँगे", "दोहराइए", "दोहराओ", "दोहरा", "दोराएंगे", "दोबारा", "सुनाई", "repeat",
              "रिपीट", "again", "pardon", "dobara", "sunai"}
REPEAT_VERB = {"बोलिए", "बोलिये", "बोलो", "बताइए", "कहिए", "boliye", "bolo"}


def _repeat(words: list[str]) -> bool:
    if not words or len(words) > 8 or any(w not in REPEAT_WORDS for w in words):
        return False
    pairs = set(zip(words, words[1:]))
    return (any(w in REPEAT_CUE for w in words) or any(p in (("फिर", "से"), ("phir", "se")) for p in pairs)
            and any(w in REPEAT_VERB for w in words) or ("क्या", "बोला") in pairs or ("क्या", "कहा") in pairs
            or ("समझ", "नहीं") in pairs)


def fast_intent(call, heard: str, unsure: bool) -> dict | None:
    """customer_said arguments for a turn code can read on its own, else None (ask the LLM)."""
    state = getattr(call, "state", None)
    ref = getattr(call, "last_ref", None) or (None, None)
    if not heard or getattr(call, "outcome", None) or ref[0] != state:
        return None
    words = _words(heard)
    allowed = set(call.spec.allowed_intents(state))
    lang = getattr(call, "lang", None) or "hi"
    # A repeat request is the safe reading even when Whisper is unsure: the bot just says the question again.
    if "repeat_request" in allowed and _repeat(words):
        return {"intent": "repeat_request", "language": lang}
    if unsure or not words:
        return None
    # "मेरे घर पे कोई plant है ही नहीं" / "wrong number": end the call now, whatever the step (CALL-786626d2)
    if (leaving := N.leaving_intent(heard)) and leaving in allowed:
        return {"intent": leaving, "language": lang}
    if len(words) > 8:
        return None
    if state in YES_NO_STATES:
        if state == "READINESS_CHECK" and not str(ref[1]).startswith("ask_"):
            return None
        intent = _yes_no(words, call, heard)
        if intent and intent in allowed:
            return {"intent": intent, "language": lang}
    if state in BAND_STATES and "gives_band" in allowed and _band(words, call, heard):
        return {"intent": "gives_band", "band": heard.strip(), "language": lang}
    return None
