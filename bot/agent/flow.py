"""Runs a YAML call flow from ../flows/<name>/ as the call's state machine.

The flow (states, transitions, prompts, policies) lives in config; this module
only executes it. The LLM reports what the customer meant via one tool
(`customer_said(intent, ...)`); code picks the next state, runs Beat Plan /
Zeus actions, and returns the exact lines to speak. The LLM never chooses
what happens next, so it cannot book early or skip the hold consent.

Framework-free: bot.py, simulate.py and the tests all drive FlowCall directly.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from . import normalize as N
from .beatplan import BeatPlan, MockBeatPlan

REPO = Path(__file__).resolve().parents[2]
DEFAULT_FLOW = "service-scheduling"


# ------------------------------------------------------------------ loading
class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps on/off/yes/no as strings (YAML 1.1 turns `on:` into True)."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def _load(path: Path):
    with path.open(encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)


@dataclass
class FlowSpec:
    dir: Path
    flow: dict
    intents: dict
    actions: dict
    policies: dict
    prompts: dict

    @classmethod
    def load(cls, name_or_dir: str | Path | None = None) -> "FlowSpec":
        target = name_or_dir or os.getenv("FLOW_DIR") or DEFAULT_FLOW
        d = Path(target)
        if not d.is_absolute() and not (d / "flow.yaml").exists():
            d = REPO / "flows" / str(target)
        policies = _load(d / "policies.yaml")
        return cls(
            dir=d,
            flow=_load(d / "flow.yaml"),
            intents=_load(d / "intents.yaml"),
            actions=_load(d / "actions.yaml"),
            policies=policies,
            prompts={lang: _load(d / "prompts" / f"{lang}.yaml") for lang in policies["languages"]},
        )

    @property
    def states(self) -> dict:
        return self.flow["states"]

    @property
    def global_intents(self) -> dict:
        return self.intents.get("global") or {}

    @property
    def local_intents(self) -> dict:
        return self.intents.get("local") or {}

    @property
    def all_intents(self) -> list[str]:
        return [*self.global_intents, *self.local_intents]

    def allowed_intents(self, state: str) -> list[str]:
        s = self.states[state]
        if s.get("type") != "conversation":
            return []
        local = [t["on"] for t in s.get("transitions", []) if t["on"] in self.local_intents]
        disabled = set(s.get("disable_global") or [])
        glob = [g for g in self.global_intents if g not in disabled]
        return list(dict.fromkeys(local + glob))


def _voice_name() -> str | None:
    """Name of the active cloned voice (/voice page), if the voice module and a voice are available."""
    try:
        from .voice import active_voice_name
        return active_voice_name() or None
    except Exception:  # noqa: BLE001 - no voice configured, or running outside the bot
        return None


# ------------------------------------------------------------------ speech helpers
HI_WEEKDAY = ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"]
HI_MONTH = ["", "जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर",
            "अक्टूबर", "नवंबर", "दिसंबर"]
HI_HOUR = ["बारह", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ", "दस", "ग्यारह", "बारह"]
HI_PART = {"morning": "सुबह", "afternoon": "दोपहर", "evening": "शाम"}
EN_PART = {"morning": "morning", "afternoon": "afternoon", "evening": "evening"}
OR = {"hi": " या ", "en": " or "}


def _hour(hhmm: str) -> int:
    return int(hhmm.split(":")[0])


def speak_weekday(d: date, lang: str) -> str:
    return HI_WEEKDAY[d.weekday()] if lang == "hi" else d.strftime("%A")


def speak_day(d: date, lang: str) -> str:
    return f"{d.day} {HI_MONTH[d.month]}" if lang == "hi" else f"{d.day} {d.strftime('%B')}"


def speak_band(band: str, policies: dict, lang: str) -> str:
    b = policies["time_bands"][band]
    s, e = _hour(b["start"]), _hour(b["end"])
    if lang == "hi":
        return f"{HI_PART[band]} {HI_HOUR[s % 12]} से {HI_HOUR[e % 12]} बजे"
    fmt = lambda h: f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"  # noqa: E731
    return f"{fmt(s)} and {fmt(e)}"


def speak_slot(slot: dict, policies: dict, lang: str) -> str:
    d = date.fromisoformat(slot["date"])
    part = (HI_PART if lang == "hi" else EN_PART)[slot["band"]]
    return f"{speak_weekday(d, lang)}, {speak_day(d, lang)}, {part}"


def parse_band(text: str, policies: dict) -> str | None:
    """Customer's words -> morning/afternoon/evening, using the policy's band hours."""
    t = (text or "").strip().lower()
    if t in policies["time_bands"]:
        return t
    tokens = set(re.sub(r"[^\w\s]", " ", t).split())
    if tokens & N.MORNING:
        part = "morning"
    elif tokens & N.AFTERNOON:
        part = "afternoon"
    elif tokens & N.EVENING:
        part = "evening"
    else:
        part = None
    m = re.search(r"\b(\d{1,2})\b", t.translate(N.DEVANAGARI_DIGITS))
    if m:
        h = int(m[1])
        if part in ("afternoon", "evening") and h < 12:
            h += 12
        elif part is None and 1 <= h <= 7:
            h += 12  # bare "4 baje" on a service call means afternoon
        for band, b in policies["time_bands"].items():
            if _hour(b["start"]) <= h < _hour(b["end"]):
                return band
    return part


# ------------------------------------------------------------------ the call
@dataclass
class Step:
    """What happened after one customer turn (or event)."""
    speak: list[str] = field(default_factory=list)
    end: bool = False
    error: str | None = None
    state: str | None = None
    allowed: list[str] = field(default_factory=list)


@dataclass
class FlowCall:
    call_id: str
    booking: dict
    spec: FlowSpec = field(default_factory=FlowSpec.load)
    beatplan: BeatPlan | None = None
    today: date = field(default_factory=N.today_ist)
    on_event: Callable[[dict], None] = lambda e: None
    auto: bool = True        # run system-state actions; False = paper mode (events come from the caller)

    state: str = ""
    lang: str = ""
    context: dict = field(default_factory=dict)      # Beat Plan input (schemas/context.schema.json)
    ctx: dict = field(default_factory=dict)          # what this call learned
    outcome: str | None = None
    silences: int = 0
    exact_time_asks: int = 0
    unclear_streak: int = 0
    readiness_idx: int = 0
    backup_queue: list | None = None
    offered: list = field(default_factory=list)
    last_line: str = ""
    path: list = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(N.IST))
    ended_at: datetime | None = None
    comment_posted: bool = False
    actions_log: list = field(default_factory=list)

    def __post_init__(self):
        self.beatplan = self.beatplan or MockBeatPlan()
        self.lang = self.lang or self.spec.policies["default_language"]
        self.ctx.setdefault("readiness", {})
        self.ctx.setdefault("missing_items", [])

    @property
    def policies(self) -> dict:
        return self.spec.policies

    @property
    def flow_version(self) -> str:
        return str(self.spec.flow.get("version"))

    # ------------------------------------------------------------ public API
    def start(self) -> Step:
        """Run PRE_CALL; returns the opening line(s), or end=True if the call must not be placed."""
        lines = self._enter(self.spec.flow["start"])
        return Step(speak=lines, end=bool(self.outcome), state=self.state,
                    allowed=self.spec.allowed_intents(self.state))

    def handle(self, on: str, entities: dict | None = None, flags: dict | None = None) -> Step:
        """Feed one intent (from the LLM) or event (paper mode) into the flow."""
        if self.outcome:
            return Step(error="call_already_ended", end=True)
        entities = {k: v for k, v in (entities or {}).items() if v not in (None, "")}
        st = self.spec.states[self.state]
        lang = entities.get("language")
        if lang in self.spec.prompts:
            self.lang = lang

        if st.get("type") == "conversation":
            allowed = self.spec.allowed_intents(self.state)
            is_event = on in self.spec.flow.get("events", [])
            if not is_event and on not in allowed:
                return Step(error=f"intent '{on}' does not apply in {self.state}", state=self.state, allowed=allowed)
            if on != "unclear":
                self.unclear_streak = 0
            self.silences = 0

        self.on_event({"type": "flow", "state": self.state, "intent": on, "entities": entities})
        self._absorb(on, entities)

        # global events (silence) and global intents, in conversation states
        if st.get("type") == "conversation":
            for ge in self.spec.flow.get("global_events") or []:
                if ge["on"] == on:
                    return self._step(self._enter(ge["to"]))
            gi = self.spec.global_intents.get(on)
            if gi and on not in (st.get("disable_global") or []):
                return self._global(on, gi)

        # state-specific behaviour before the transition
        pre = self._before_transition(on, entities)
        if pre is not None:
            return pre

        tr = self._pick(self.state, on, flags)
        if tr is None:
            return Step(error=f"no transition for '{on}' in {self.state}", state=self.state,
                        allowed=self.spec.allowed_intents(self.state))
        self._apply_set(tr)
        return self._step(self._enter(tr["to"], tr.get("say")))

    def silence(self) -> Step:
        """Customer said nothing for the idle timeout."""
        if self.outcome:
            return Step(end=True)
        self.silences += 1
        if self.silences > self.policies["silence"]["max_reprompts"]:
            return self.handle("silence_timeout")
        return self._step([f"{self._global_line('reprompt')} {self.last_line}".strip()], track=False)

    def hang_up(self, outcome: str = "hung_up") -> None:
        """Line dropped before a terminal state: still log the call to Beat Plan."""
        if not self.outcome:
            self.outcome = outcome
            self.ended_at = datetime.now(N.IST)
            self._post_call()

    # ------------------------------------------------------------ transitions
    def _flags(self) -> dict:
        return {
            "exact_time_limit_reached": self.exact_time_asks > self.policies["exact_time_insist_limit"],
            "options_remaining": bool(self.backup_queue),
            "reschedule_after_ready_date": self.policies["on_missing_prereq"] == "reschedule_after_ready_date",
        }

    def _pick(self, state: str, on: str, flags: dict | None) -> dict | None:
        f = {**self._flags(), **(flags or {})}
        for tr in self.spec.states[state].get("transitions", []):
            if tr["on"] != on:
                continue
            when = tr.get("when")
            if when is not None and bool(f.get(str(when).lstrip("!"))) == str(when).startswith("!"):
                continue
            return tr
        return None

    def _apply_set(self, src: dict) -> None:
        for k, v in (src.get("set") or {}).items():
            self.ctx[k] = self.context.get(v, v) if isinstance(v, str) else v
            if k == "selected_slot" and isinstance(self.ctx[k], dict):
                self._emit_slot()

    def _enter(self, target: str, say: str | None = None) -> list[str]:
        lines: list[str] = []
        while True:
            self.state = target
            self.path.append(target)
            st = self.spec.states[target]
            kind = st.get("type")
            self.on_event({"type": "flow_state", "state": target})

            if kind == "system":
                if not self.auto:
                    return lines
                ev = self._run_actions(st.get("actions") or [])
                tr = self._pick(target, ev, None)
                if tr is None:
                    raise RuntimeError(f"{target}: no transition for event {ev}")
                self._apply_set(tr)
                target, say = tr["to"], tr.get("say")
                continue

            if kind == "conversation":
                if self.auto and st.get("actions"):
                    ev = self._run_actions(st["actions"])
                    tr = self._pick(target, ev, None) if ev != "action_ok" else None
                    if tr is not None:
                        self._apply_set(tr)
                        target, say = tr["to"], tr.get("say")
                        continue
                if st.get("checklist") and say is None:
                    self.readiness_idx = 0
                    lines.append(self._line(target, "main"))
                    lines.append(self._checklist_question())
                else:
                    lines.append(self._line(target, say or "main"))
                self.last_line = lines[-1]
                return lines

            # terminal
            outcome = st.get("outcome") or (st.get("outcome_map") or {}).get(self.ctx.get(st.get("outcome_from")))
            if self.auto:
                self._run_actions(st.get("actions") or [])
            if not st.get("silent"):
                lines.append(self._line(target, "main"))
            self.outcome = outcome
            self.ended_at = datetime.now(N.IST)
            self._post_call()
            return lines

    def _step(self, lines: list[str], track: bool = True) -> Step:
        return Step(speak=lines, end=bool(self.outcome), state=self.state,
                    allowed=self.spec.allowed_intents(self.state))

    def _global(self, name: str, gi: dict) -> Step:
        if name == "unclear":
            self.unclear_streak += 1
            if gi.get("escalate_to") and self.unclear_streak > int(gi.get("escalate_after", 2)):
                self.ctx["rm_callback_reason"] = "repeated_misunderstanding"
                return self._step(self._enter(gi["escalate_to"]))
        self._apply_set(gi)
        if name == "not_serviceable" and self.ctx.get("_date") and self.auto:
            self._enter(gi["goto"])          # date already given: skip "from which date?"
            return self.handle("date_given")
        if gi.get("goto") == "$stay":
            return self._step([f"{self._global_line(gi.get('say'))} {self.last_line}".strip()])
        return self._step(self._enter(gi["goto"]))

    # ------------------------------------------------------------ per-state rules
    def _absorb(self, on: str, e: dict) -> None:
        """Store entities the LLM extracted, parsed by code (never trusted as-is)."""
        if e.get("exact_time"):
            self.ctx["preferred_exact_time"] = e["exact_time"]
        if e.get("band"):
            self.ctx["preferred_band"] = parse_band(e["band"], self.policies)
        if e.get("reason"):
            self.ctx["hold_reason"] = self.ctx.get("hold_reason") or e["reason"]
            if on == "wants_human":
                self.ctx["rm_callback_reason"] = e["reason"]
        if e.get("time_range"):
            self.ctx["recall_window"] = e["time_range"]
        if e.get("date"):
            d = N.parse_date(e["date"], self.today)
            if d:
                self.ctx["_date"] = d
        if on == "not_serviceable":
            self.ctx["hold_reason"] = e.get("reason") or "not_serviceable"
            if self.ctx.get("_date"):
                self.ctx["hold_until"] = self.ctx["_date"].isoformat()
        if on == "wants_exact_time" and self.state == "TIME_BAND":
            self.exact_time_asks += 1

    def _before_transition(self, on: str, e: dict) -> Step | None:
        if not self.auto:
            return None
        st = self.spec.states[self.state]

        if st.get("checklist") and on in ("affirm", "deny"):
            items = self.policies[st["checklist"]]
            item = items[self.readiness_idx]
            self.ctx["readiness"][item["id"]] = on == "affirm"
            if on == "deny":
                self.ctx["missing_items"].append(item["id"])
            self._emit_readiness(item["id"], on == "affirm")
            self.readiness_idx += 1
            if self.readiness_idx < len(items):
                q = self._checklist_question()
                self.last_line = q
                return self._step([q])
            final = "deny" if self.ctx["missing_items"] else "affirm"
            if final == "deny":
                self.ctx["hold_reason"] = "prerequisites_missing"
            tr = self._pick(self.state, final, None)
            self._apply_set(tr)
            return self._step(self._enter(tr["to"], tr.get("say")))

        if self.state == "ALTERNATE_DAYS" and on == "affirm":
            chosen = self._resolve_option(e)
            if chosen is None:
                return self._step([f"{self._global_line('didnt_catch')} {self.last_line}".strip()])
            self.ctx["selected_slot"] = chosen
            self._emit_slot()

        if on == "date_given":
            d = self.ctx.pop("_date", None)
            if d is None or d <= self.today:
                return self._step([f"{self._global_line('didnt_catch')} {self.last_line}".strip()])
            self.ctx["hold_until"] = d.isoformat()

        if on == "time_range_given" and not self.ctx.get("recall_window"):
            return self._step([f"{self._global_line('didnt_catch')} {self.last_line}".strip()])

        if on == "wants_other_band" and not self.ctx.get("preferred_band"):
            return self._step([f"{self._global_line('didnt_catch')} {self.last_line}".strip()])
        return None

    def _resolve_option(self, e: dict) -> dict | None:
        if not self.offered:
            return None
        opt = e.get("option")
        if isinstance(opt, (int, float, str)) and str(opt).isdigit() and 1 <= int(opt) <= len(self.offered):
            return self.offered[int(opt) - 1]
        d = self.ctx.pop("_date", None)
        if d:
            for s in self.offered:
                if s["date"] == d.isoformat():
                    return s
            return None
        return self.offered[0] if len(self.offered) == 1 else None

    def _checklist_question(self) -> str:
        st = self.spec.states[self.state]
        item = self.policies[st["checklist"]][self.readiness_idx]
        return self._line(self.state, f"ask_{item['id']}")

    # ------------------------------------------------------------ actions
    def _run_actions(self, names: list[str]) -> str:
        """Run actions in order; the first that returns an event decides. Default: action_ok."""
        for name in names:
            ev = self.beatplan.run(name, self, self.state)
            if ev:
                return ev
        return "action_ok"

    def _report(self, name: str, payload: dict) -> None:
        """An action that would hit Beat Plan / Zeus: logged and shown on the dashboard."""
        status = self.spec.actions.get(name, {}).get("status")
        self.actions_log.append({"action": name, "status": status, "payload": payload})
        self.on_event({"type": "beatplan", "action": name, "status": status, "payload": payload})

    def _post_call(self) -> None:
        if self.comment_posted:
            return
        self.comment_posted = True
        if not self.auto:
            return
        for name in (self.spec.flow.get("post_call") or {}).get("actions", []):
            self.beatplan.run(name, self, self.state)

    # ------------------------------------------------------------ lines
    def _vars(self) -> dict:
        v: dict[str, Any] = defaultdict(str)
        name = (self.context.get("customer", {}).get("name") or self.booking.get("customerName") or "").split(" ")[0]
        v["name"] = name
        v["bot_name"] = _voice_name() or self.policies["persona"]["name"]
        slot = self.ctx.get("selected_slot") or self.context.get("primary_slot")
        if isinstance(slot, dict):
            d = date.fromisoformat(slot["date"])
            band = self.ctx.get("preferred_band") if self.ctx.get("slot_source") == "alt_band" else slot["band"]
            v["weekday"] = speak_weekday(d, self.lang)
            v["date_spoken"] = speak_day(d, self.lang)
            v["band_spoken"] = speak_band(band or slot["band"], self.policies, self.lang)
        v["options_spoken"] = OR[self.lang].join(speak_slot(s, self.policies, self.lang) for s in self.offered)
        labels = {i["id"]: i["labels"][self.lang] for i in self.policies["readiness_items"]}
        miss = [labels[m] for m in self.ctx["missing_items"]]
        v["missing_item"] = (" और " if self.lang == "hi" else " and ").join(miss) if miss else ""
        if self.ctx.get("hold_until"):
            d = date.fromisoformat(self.ctx["hold_until"])
            v["hold_until_spoken"] = speak_day(d, self.lang)
        v["recall_window_spoken"] = self.ctx.get("recall_window") or ""
        return v

    def _line(self, state: str, key: str) -> str:
        p = self.spec.prompts[self.lang].get(state) or {}
        return (p.get(key) or "").format_map(self._vars())

    def _global_line(self, key: str | None) -> str:
        return ((self.spec.prompts[self.lang].get("_global") or {}).get(key or "") or "").format_map(self._vars())

    # ------------------------------------------------------------ dashboard + record
    def _emit_slot(self) -> None:
        s = self.ctx.get("selected_slot")
        if not isinstance(s, dict):
            return
        band = self.ctx.get("preferred_band") if self.ctx.get("slot_source") == "alt_band" else s["band"]
        d = date.fromisoformat(s["date"])
        self.on_event({"type": "slot", "key": "visit_date", "ok": True, "value": s["date"],
                       "display": f"{d.strftime('%a %d %b')}"})
        self.on_event({"type": "slot", "key": "visit_time_slot", "ok": True, "value": band,
                       "display": speak_band(band or s["band"], self.policies, "en")})

    def _emit_readiness(self, item: str, ok: bool) -> None:
        key = {"ladder": "ladder_available", "water": "water_source_available", "light": "light_available"}.get(
            item, f"{item}_available")
        self.on_event({"type": "slot", "key": key, "ok": True, "value": ok, "display": "yes" if ok else "no"})

    def confirmed_slot(self) -> dict | None:
        if not (self.outcome or "").startswith("slot_confirmed"):
            return None
        s = self.ctx.get("selected_slot") or {}
        band = self.ctx.get("preferred_band") if self.ctx.get("slot_source") == "alt_band" else s.get("band")
        return {"date": s.get("date"), "band": band, "hero_id": s.get("hero_id")}

    def summary(self) -> str:
        c, o = self.ctx, self.outcome
        parts = [f"Outcome: {o}."]
        if (cs := self.confirmed_slot()):
            parts.append(f"Confirmed {cs['date']} {cs['band']} ({c.get('slot_source')}).")
        if c.get("missing_items"):
            parts.append(f"Missing: {', '.join(c['missing_items'])}.")
        if c.get("hold_until"):
            parts.append(f"Hold until {c['hold_until']} ({c.get('hold_reason')}), consent={c.get('hold_consent')}.")
        if c.get("preferred_exact_time"):
            parts.append(f"Asked for exact time: {c['preferred_exact_time']}.")
        if c.get("recall_window"):
            parts.append(f"Call back: {c['recall_window']}.")
        if c.get("rm_callback_reason"):
            parts.append(f"RM callback: {c['rm_callback_reason']}.")
        return " ".join(parts)

    def record(self) -> dict:
        """Call record (schemas/call-record.schema.json) + `slots` for the dashboard."""
        ended = self.ended_at or datetime.now(N.IST)
        c = self.ctx
        cs = self.confirmed_slot()
        readiness = {i["id"]: c["readiness"].get(i["id"]) for i in self.policies["readiness_items"]}
        hold = None
        if c.get("hold_until") or c.get("hold_consent") is not None:
            hold = {"reason": c.get("hold_reason"), "missing_items": c.get("missing_items", []),
                    "until": c.get("hold_until"), "consent": bool(c.get("hold_consent"))}
        return {
            "call_id": self.call_id,
            "site_id": self.context.get("site_id") or self.booking.get("sseId"),
            "booking_id": self.booking.get("id"),
            "flow_version": self.flow_version,
            "language": self.lang,
            "outcome": self.outcome,
            "spoke_to": c.get("spoke_to", "nobody"),
            "confirmed_slot": cs,
            "slot_source": c.get("slot_source") if cs else None,
            "preferred_exact_time": c.get("preferred_exact_time"),
            "readiness": readiness,
            "hold": hold,
            "recall_window": c.get("recall_window"),
            "rm_callback_reason": c.get("rm_callback_reason"),
            "summary": self.summary(),
            "path": self.path,
            "slots": {
                "visit_date": cs["date"] if cs else None,
                "visit_time_slot": cs["band"] if cs else None,
                "ladder_available": readiness.get("ladder"),
                "water_source_available": readiness.get("water"),
                "light_available": readiness.get("light"),
            },
            "started_at": self.started_at.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_s": round((ended - self.started_at).total_seconds(), 1),
        }
