"""Tool schemas + framework-free dispatch. bot.py and simulate.py both use dispatch()."""
from __future__ import annotations

from dataclasses import dataclass, field

from .prompts import goodbye
from .slots import AGENT_OUTCOMES, SLOT_KEYS, SLOTS, CallState

TOOLS = [
    {
        "name": "save_slot",
        "description": "Save one detail the customer gave. Call once per detail as soon as you hear it.",
        "properties": {
            "key": {"type": "string", "enum": SLOT_KEYS,
                    "description": " | ".join(f"{s.key}: {s.describe}" for s in SLOTS)},
            "value": {"type": "string", "description": "Customer's words or exact value (YYYY-MM-DD, 09-12/12-15/15-18, true/false)."},
        },
        "required": ["key", "value"],
    },
    {
        "name": "read_back",
        "description": "Read all saved details back to the customer (system speaks them). Use after a correction.",
        "properties": {},
        "required": [],
    },
    {
        "name": "confirm_details",
        "description": "Record the customer's answer to the read-back: true if they said everything is correct.",
        "properties": {"confirmed": {"type": "boolean"}},
        "required": ["confirmed"],
    },
    {
        "name": "end_call",
        "description": "End the call early. Not for confirmed calls (the system ends those).",
        "properties": {
            "outcome": {"type": "string", "enum": sorted(AGENT_OUTCOMES - {"confirmed"})},
            "callback_time": {"type": "string", "description": "Required for callback, e.g. 'kal shaam 5 baje'."},
        },
        "required": ["outcome"],
    },
    {
        "name": "transfer_to_human",
        "description": "Hand the call to a human team member.",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
    },
]


@dataclass
class Action:
    result: dict                     # returned to the LLM
    speak: list[str] = field(default_factory=list)   # spoken verbatim by TTS, in order
    end: bool = False                # end the call after speaking
    run_llm: bool = True             # let the LLM respond to the result
    ask_next: str | None = None      # spoken after this turn's tool calls (skips a 2nd LLM round trip)
    idle_timeout: float | None = None  # lengthen the silence timer (customer asked us to hold)


ACKS = ["Theek hai.", "Ji, bilkul.", "Achha ji."]


def _end(state: CallState, outcome: str, callback_time: str | None = None, system: bool = False,
         prefix: list[str] | None = None) -> Action:
    r = state.try_end(outcome, callback_time, system=system)
    if not r["ok"]:
        return Action(result=r)
    return Action(result=r, speak=(prefix or []) + [goodbye(outcome, callback_time)], end=True, run_llm=False)


def dispatch(state: CallState, name: str, args: dict) -> Action:
    if state.outcome:
        return Action(result={"ok": False, "error": "call_already_ended"}, run_llm=False)

    if name == "save_slot":
        r = state.save(args.get("key", ""), args.get("value", ""))
        if not r["ok"]:
            if r.get("escalate"):
                return _end(state, "handed_to_human", system=True)
            return Action(result=r)
        if not state.missing() and not state.read_back:
            text = state.read_back_text()
            return Action(result={**r, "read_back_spoken": text,
                                  "next": "Wait for the customer's answer, then call confirm_details."},
                          speak=[text], run_llm=False)
        q = state.next_question()
        if q and not state.read_back:  # fixed next question: speak it directly, no 2nd LLM call
            return Action(result={**r, "next_question_spoken": q}, run_llm=False,
                          ask_next=f"{ACKS[len(state.values) % len(ACKS)]} {q}")
        return Action(result={**r, "next_question": q})

    if name == "read_back":
        if state.missing():
            return Action(result={"ok": False, "error": "missing_slots", "next_question": state.next_question()})
        text = state.read_back_text()
        return Action(result={"ok": True, "read_back_spoken": text}, speak=[text], run_llm=False)

    if name == "confirm_details":
        r = state.confirm(bool(args.get("confirmed")))
        if not r["ok"]:
            if r["error"] == "missing_slots":
                r["next_question"] = state.next_question()
            return Action(result=r)
        if r["confirmed"]:
            return _end(state, "confirmed")
        return Action(result={**r, "next": "Ask which detail is wrong, save the corrected value, then call read_back."})

    if name == "end_call":
        outcome = args.get("outcome", "")
        if outcome == "confirmed":
            return Action(result={"ok": False, "error": "use_confirm_details"})
        return _end(state, outcome, args.get("callback_time"))

    if name == "transfer_to_human":
        return _end(state, "handed_to_human", system=True)

    return Action(result={"ok": False, "error": f"unknown_tool:{name}"})


def on_silence(state: CallState, max_silences: int = 3) -> Action:
    """Called when the customer says nothing for the idle timeout."""
    from .prompts import SILENCE_PROMPTS

    state.silences += 1
    if state.silences >= max_silences:
        return _end(state, "no_answer", system=True)
    lead = SILENCE_PROMPTS[(state.silences - 1) % len(SILENCE_PROMPTS)]
    follow = state.next_question() if not state.read_back else "Kya maine jo details boli, woh sahi hain?"
    return Action(result={}, speak=[f"{lead} {follow}"], run_llm=False)


# ------------------------------------------------------------------ YAML flow mode
def flow_tools(spec) -> list[dict]:
    """The one tool the LLM uses when a YAML flow runs the call."""
    return [{
        "name": "customer_said",
        "description": "Report what the customer just said. Call on every customer turn. The system speaks next.",
        "properties": {
            "intent": {"type": "string", "enum": spec.all_intents},
            "date": {"type": "string", "description": "Customer's words for a date, if any."},
            "option": {"type": "integer", "description": "1 or 2: which offered day they picked."},
            "band": {"type": "string", "description": "Preferred part of day in their words."},
            "exact_time": {"type": "string", "description": "Exact time they asked for."},
            "time_range": {"type": "string", "description": "When to call back, in their words."},
            "reason": {"type": "string", "description": "Why not serviceable / why they want a person."},
            "language": {"type": "string", "enum": spec.policies["languages"]},
        },
        "required": ["intent"],
    }]
