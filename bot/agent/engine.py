"""One interface over the two call engines, so bot.py and simulate.py don't care which runs.

  CALL_FLOW=service-scheduling (default)  -> FlowEngine: YAML flow in ../flows/service-scheduling
  CALL_FLOW=checklist                     -> ChecklistEngine: the original 4-key slot checklist
  CALL_FLOW=<other folder in flows/>      -> FlowEngine on that flow
"""
from __future__ import annotations

import os
from datetime import date
from typing import Callable

from . import normalize as N
from .flow import FlowCall, FlowSpec
from .prompts import flow_system_prompt, goodbye, greeting, system_prompt
from .slots import CallState
from .tools import TOOLS, Action, dispatch, flow_tools, on_silence

_noop: Callable[[dict], None] = lambda e: None  # noqa: E731


class ChecklistEngine:
    name = "checklist"

    def __init__(self, call_id: str, booking: dict, on_event=_noop, today: date | None = None):
        self.state = CallState(call_id=call_id, booking=booking, on_event=on_event, today=today or N.today_ist())
        self.booking = booking
        self.tools = TOOLS

    @property
    def outcome(self):
        return self.state.outcome

    def opener(self) -> str:
        return "Hello?"

    def opening(self) -> list[str]:
        return [greeting(self.booking)]

    def system_prompt(self) -> str:
        return system_prompt(self.booking, self.state.today)

    def dispatch(self, name: str, args: dict) -> Action:
        return dispatch(self.state, name, args)

    def on_silence(self) -> Action:
        return on_silence(self.state)

    def heard_customer(self) -> None:
        self.state.silences = 0

    def hang_up(self) -> None:
        if not self.state.outcome:
            self.state.outcome = "hung_up"

    def record(self) -> dict:
        return {**self.state.record(), "flow": "checklist"}


class FlowEngine:
    def __init__(self, call_id: str, booking: dict, on_event=_noop, today: date | None = None,
                 flow: str | None = None, knowledge: dict | None = None):
        spec = FlowSpec.load(flow)
        self.name = spec.dir.name
        self.call = FlowCall(call_id=call_id, booking=booking, spec=spec, on_event=on_event,
                             today=today or N.today_ist(), knowledge=knowledge or {})
        self._start = self.call.start()     # runs PRE_CALL (context fetch, eligibility)
        self.tools = flow_tools(spec, knowledge)

    @property
    def outcome(self):
        return self.call.outcome

    def opener(self) -> str:
        """The first words of the call ("नमस्ते जी?"), said before the customer speaks; the flow's _global.opener."""
        return self.call._global_line("opener") or "Hello?"

    def opening(self) -> list[str]:
        return self._start.speak

    @property
    def blocked(self) -> bool:
        """True if PRE_CALL decided the call must not happen (DNC, allowlist, hours)."""
        return self._start.end

    def system_prompt(self) -> str:
        return flow_system_prompt(self.call)

    def _action(self, step) -> Action:
        if step.error:
            return Action(result={"ok": False, "error": step.error, "state": step.state,
                                  "allowed_intents": step.allowed}, run_llm=True)
        has_faqs = bool(self.call.knowledge.get("faqs"))
        allowed = [i for i in step.allowed if i != "known_question" or has_faqs]
        return Action(result={"ok": True, "state": step.state, "bot_said": step.speak,
                              "allowed_intents": allowed, "ended": step.end},
                      speak=step.speak, end=step.end, run_llm=False, idle_timeout=step.idle_timeout,
                      offguard=step.offguard)

    def dispatch(self, name: str, args: dict) -> Action:
        if self.call.outcome:
            return Action(result={"ok": False, "error": "call_already_ended"}, run_llm=False)
        if name != "customer_said":
            return Action(result={"ok": False, "error": f"unknown_tool:{name}",
                                  "allowed_intents": self.call.spec.allowed_intents(self.call.state)})
        args = dict(args)
        intent = args.pop("intent", "")
        if intent not in self.call.spec.all_intents:
            return Action(result={"ok": False, "error": f"unknown intent '{intent}'", "state": self.call.state,
                                  "allowed_intents": self.call.spec.allowed_intents(self.call.state)})
        return self._action(self.call.handle(intent, args))

    def on_silence(self) -> Action:
        return self._action(self.call.silence())

    def heard_customer(self) -> None:
        self.call.silences = 0

    def hang_up(self) -> None:
        self.call.hang_up("hung_up")

    def record(self) -> dict:
        return {**self.call.record(), "flow": self.name}


def make_engine(call_id: str, booking: dict, on_event=_noop, today: date | None = None,
                mode: str | None = None, knowledge: dict | None = None):
    mode = mode or os.getenv("CALL_FLOW", "service-scheduling")
    if mode == "checklist":
        return ChecklistEngine(call_id, booking, on_event, today)
    return FlowEngine(call_id, booking, on_event, today, flow=mode, knowledge=knowledge)


__all__ = ["ChecklistEngine", "FlowEngine", "make_engine", "goodbye"]
