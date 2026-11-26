"""Config-driven slot engine: the single source of truth for what a call must collect.

Hard rule enforced here (not in the prompt): the call cannot close as
`confirmed` until every slot is saved, read back to the customer, and the
customer said yes to the read-back. Any change after that forces a re-read.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from . import normalize as N

MAX_DAYS_AHEAD = 30
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Slot:
    key: str
    question: str           # how the agent asks for it (Hinglish)
    describe: str           # for the LLM tool schema
    parse: Callable[[str, date], tuple[Any, str | None]]   # -> (value, error_code)
    validate: Callable[[Any, date], str | None]            # -> error_code or None
    speak: Callable[[Any], str]                            # value -> Hinglish phrase
    to_json: Callable[[Any], Any] = lambda v: v


def _parse_date(raw: str, today: date):
    d = N.parse_date(raw, today)
    return (d, None) if d else (None, "unclear")


def _validate_date(d: date, today: date):
    if d < today:
        return "past_date"
    if d == today:
        return "same_day"
    if d.weekday() == 6:
        return "sunday"
    if d > today + timedelta(days=MAX_DAYS_AHEAD):
        return "too_far"
    return None


def _parse_slot(raw: str, today: date):
    return N.parse_time_slot(raw)


def _parse_bool(raw: str, today: date):
    b = N.parse_bool(raw)
    return (b, None) if b is not None else (None, "unclear")


SLOTS: list[Slot] = [
    Slot(
        key="visit_date",
        question="Visit kis din rakhein? Somvar se Shanivar, agle 30 din mein koi bhi din.",
        describe="Visit date. Pass the customer's words (e.g. 'kal', 'agle somvar', '5 tarikh') or YYYY-MM-DD.",
        parse=_parse_date,
        validate=_validate_date,
        speak=N.speak_date,
        to_json=lambda d: d.isoformat(),
    ),
    Slot(
        key="visit_time_slot",
        question="Kaunsa time theek rahega: subah 9 se 12, dopahar 12 se 3, ya shaam 3 se 6?",
        describe="Time window. Pass the customer's words (e.g. 'subah', '3 baje') or one of 09-12, 12-15, 15-18.",
        parse=_parse_slot,
        validate=lambda v, today: None if v in N.SLOT_IDS else "unclear",
        speak=lambda v: N.SLOT_SPEECH[v],
    ),
    Slot(
        key="ladder_available",
        question="Chhat tak jaane ke liye ghar pe seedhi available hai kya?",
        describe="Is a ladder available to reach the panels? Pass the customer's words or true/false.",
        parse=_parse_bool,
        validate=lambda v, today: None,
        speak=lambda v: "seedhi available hai" if v else "seedhi available nahi hai",
    ),
    Slot(
        key="water_source_available",
        question="Panels saaf karne ke liye chhat pe ya paas mein paani ka nal hai kya?",
        describe="Is a water tap/source near the panels? Pass the customer's words or true/false.",
        parse=_parse_bool,
        validate=lambda v, today: None,
        speak=lambda v: "paani ka source hai" if v else "paani ka source nahi hai",
    ),
]
SLOT_BY_KEY = {s.key: s for s in SLOTS}
SLOT_KEYS = [s.key for s in SLOTS]

# Why a value was rejected, said back to the customer (and shown on the dashboard).
ERROR_TEXT = {
    "unclear": "samajh nahi aaya",
    "past_date": "yeh date nikal chuki hai",
    "same_day": "aaj ka visit possible nahi hai, kal ya uske baad ka din boliye",
    "sunday": "Ravivar ko visit nahi hota, Somvar se Shanivar mein koi din boliye",
    "too_far": "hum sirf agle 30 din ke andar visit rakh sakte hain",
    "out_of_hours": "visit sirf subah 9 se shaam 6 baje ke beech ho sakta hai",
    "unknown_key": "unknown key",
}

AGENT_OUTCOMES = {"confirmed", "do_not_call", "wrong_number", "callback"}
SYSTEM_OUTCOMES = {"no_answer", "handed_to_human"}
ALL_OUTCOMES = AGENT_OUTCOMES | SYSTEM_OUTCOMES


@dataclass
class CallState:
    call_id: str
    booking: dict = field(default_factory=dict)
    today: date = field(default_factory=N.today_ist)
    on_event: Callable[[dict], None] = lambda e: None

    values: dict[str, Any] = field(default_factory=dict)
    attempts: Counter = field(default_factory=Counter)
    read_back: bool = False
    confirmed: bool = False
    outcome: str | None = None
    callback_time: str | None = None
    silences: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(N.IST))
    ended_at: datetime | None = None

    # ------------------------------------------------------------ slots
    def missing(self) -> list[str]:
        return [k for k in SLOT_KEYS if k not in self.values]

    def next_question(self) -> str | None:
        miss = self.missing()
        return SLOT_BY_KEY[miss[0]].question if miss else None

    def save(self, key: str, raw: Any) -> dict:
        slot = SLOT_BY_KEY.get(key)
        if not slot:
            return {"ok": False, "key": key, "error": "unknown_key", "say": ERROR_TEXT["unknown_key"]}
        raw_s = str(raw).strip() if not isinstance(raw, bool) else ("haan" if raw else "nahi")
        value, err = slot.parse(raw_s, self.today)
        if err is None:
            err = slot.validate(value, self.today)
        if err:
            self.attempts[key] += 1
            res = {"ok": False, "key": key, "raw": raw_s, "error": err, "say": ERROR_TEXT.get(err, err),
                   "attempts": self.attempts[key], "escalate": self.attempts[key] >= MAX_ATTEMPTS}
            self.on_event({"type": "slot", "key": key, "ok": False, "raw": raw_s, "error": ERROR_TEXT.get(err, err)})
            return res

        changed = key in self.values and self.values[key] != value
        self.values[key] = value
        self.attempts[key] = 0
        if changed or self.read_back:
            # anything said after a read-back must be read back again
            self.read_back = False
            self.confirmed = False
        display = slot.speak(value)
        self.on_event({"type": "slot", "key": key, "ok": True, "value": slot.to_json(value), "display": display})
        return {"ok": True, "key": key, "value": slot.to_json(value), "display": display,
                "missing": self.missing()}

    # ------------------------------------------------------------ read-back & confirm
    def read_back_text(self) -> str:
        if self.missing():
            raise ValueError("cannot read back with missing slots")
        v = self.values
        self.read_back = True
        self.confirmed = False
        self.on_event({"type": "readback"})
        return (
            f"Main ek baar confirm kar leti hoon: visit {N.speak_date(v['visit_date'])} ko, "
            f"{N.SLOT_SPEECH[v['visit_time_slot']]}. "
            f"{SLOT_BY_KEY['ladder_available'].speak(v['ladder_available']).capitalize()}, aur "
            f"{SLOT_BY_KEY['water_source_available'].speak(v['water_source_available'])}. "
            "Kya yeh sab sahi hai?"
        )

    def confirm(self, yes: bool) -> dict:
        if self.missing():
            return {"ok": False, "error": "missing_slots", "missing": self.missing()}
        if not self.read_back:
            return {"ok": False, "error": "not_read_back"}
        self.confirmed = bool(yes)
        self.on_event({"type": "confirm", "confirmed": self.confirmed})
        if not yes:
            self.read_back = False
        return {"ok": True, "confirmed": self.confirmed}

    # ------------------------------------------------------------ ending
    def try_end(self, outcome: str, callback_time: str | None = None, system: bool = False) -> dict:
        if self.outcome:
            return {"ok": False, "error": "already_ended", "outcome": self.outcome}
        allowed = ALL_OUTCOMES if system else AGENT_OUTCOMES
        if outcome not in allowed:
            return {"ok": False, "error": "outcome_not_allowed", "allowed": sorted(allowed)}
        if outcome == "confirmed":
            if self.missing():
                return {"ok": False, "error": "missing_slots", "missing": self.missing(),
                        "next_question": self.next_question()}
            if not self.read_back:
                return {"ok": False, "error": "not_read_back"}
            if not self.confirmed:
                return {"ok": False, "error": "customer_has_not_confirmed"}
        if outcome == "callback" and not (callback_time or "").strip():
            return {"ok": False, "error": "callback_time_required"}
        self.outcome = outcome
        self.callback_time = callback_time
        self.ended_at = datetime.now(N.IST)
        return {"ok": True, "outcome": outcome}

    def record(self) -> dict:
        ended = self.ended_at or datetime.now(N.IST)
        return {
            "call_id": self.call_id,
            "booking_id": self.booking.get("id"),
            "outcome": self.outcome,
            "slots": {k: (SLOT_BY_KEY[k].to_json(self.values[k]) if k in self.values else None) for k in SLOT_KEYS},
            "callback_time": self.callback_time,
            "confirmed": self.confirmed,
            "started_at": self.started_at.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_s": round((ended - self.started_at).total_seconds(), 1),
        }
