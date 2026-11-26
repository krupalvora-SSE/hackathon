"""Beat Plan / Zeus actions the flow triggers (flows/<name>/actions.yaml).

The real APIs are `contract_pending`, so MockBeatPlan reads the booking's
`beatPlan` block (app/data/bookings.json) and reports every action as an
`action` event, so the dashboard shows exactly what would be sent. Swap in a
real client by implementing the same `run(name, call, state)` method.

Each handler returns an event name (e.g. "band_full") or None ("fine, carry on").
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol

from . import normalize as N

if TYPE_CHECKING:
    from .flow import FlowCall

WINDOW_TO_BAND = {"09-12": "morning", "12-15": "afternoon", "15-18": "evening"}


class BeatPlan(Protocol):
    def run(self, name: str, call: "FlowCall", state: str) -> str | None: ...


def context_from_booking(b: dict) -> dict:
    """Beat Plan context (schemas/context.schema.json) from an app booking row."""
    bp = b.get("beatPlan") or {}
    primary = bp.get("primary_slot") or (
        {"date": b["date"], "band": WINDOW_TO_BAND.get(b.get("window", ""), "morning"), "hero_id": bp.get("hero_id", "SH-?")}
        if b.get("date") else None)
    return {
        "site_id": b.get("sseId") or b.get("id"),
        "customer": {"customer_id": b.get("customerId") or b.get("id"), "name": b.get("customerName", ""),
                     "phone": b.get("phone", ""), "dnc": bool(b.get("dnc", False))},
        "service_type": bp.get("service_type", "panel cleaning"),
        "primary_slot": primary,
        "backup_slots": sorted(bp.get("backup_slots") or [], key=lambda s: s["date"]),
        "availability": bp.get("availability") or {},
        "plan_window_end": bp.get("plan_window_end"),
        "fail_actions": bp.get("fail_actions") or [],
        "last_visit_missed": bool(bp.get("last_visit_missed", False)),
        "last_call_at": bp.get("last_call_at"),
        "calls_this_week": int(bp.get("calls_this_week", 0)),
    }


class MockBeatPlan:
    def run(self, name: str, call: "FlowCall", state: str) -> str | None:
        handler = getattr(self, name, None)
        if handler is None:
            raise KeyError(f"no handler for action '{name}'")
        if name in (call.context.get("fail_actions") or []):
            return "action_failed"
        return handler(call, state)

    # ------------------------------------------------------------ pre-call
    def fetch_beatplan_context(self, call: "FlowCall", state: str):
        call.context = context_from_booking(call.booking)
        if not call.context["primary_slot"]:
            return "not_eligible"
        return None

    def check_call_eligibility(self, call: "FlowCall", state: str):
        c = call.context["customer"]
        if c["dnc"]:
            call.ctx["blocked_reason"] = "dnc"
            return "not_eligible"
        allow = [n.strip() for n in os.getenv("ALLOWED_TEST_NUMBERS", "").split(",") if n.strip()]
        if allow and c["phone"] and c["phone"] not in allow:
            call.ctx["blocked_reason"] = "not_test_number"
            return "not_eligible"
        limits = call.policies.get("call_limits") or {}
        if call.context.get("calls_this_week", 0) >= int(limits.get("max_calls_per_week", 99)):
            call.ctx["blocked_reason"] = "too_many_calls_this_week"
            return "not_eligible"
        last = call.context.get("last_call_at")
        if last:
            hours = (datetime.now(N.IST) - datetime.fromisoformat(last)).total_seconds() / 3600
            if hours < float(limits.get("min_hours_between_calls", 0)):
                call.ctx["blocked_reason"] = "called_recently"
                return "not_eligible"
        if os.getenv("ENFORCE_CALLING_HOURS") == "1":
            h = call.policies["calling_hours"]
            now = datetime.now(N.IST).strftime("%H:%M")
            if not (h["start"] <= now < h["end"]):
                call.ctx["blocked_reason"] = "outside_calling_hours"
                return "not_eligible"
        return "context_ready"

    # ------------------------------------------------------------ slots
    def check_band_availability(self, call: "FlowCall", state: str):
        base = call.ctx.get("selected_slot") or call.context["primary_slot"]
        band = call.ctx.get("preferred_band")
        free = call.context["availability"].get(base["date"])
        ok = band in free if free is not None else band == base["band"]
        call._report("check_band_availability", {"date": base["date"], "band": band, "free": ok})
        if not ok:
            return "band_full"
        call.ctx["selected_slot"] = {**base, "band": band}
        call._emit_slot()
        return "band_available"

    def get_backup_slots(self, call: "FlowCall", state: str):
        backups = call.context["backup_slots"]
        if state == "FIND_SLOT_AFTER_READY":
            ready = call.ctx.get("hold_until")
            nxt = next((s for s in backups if ready and s["date"] > ready), None)
            if not nxt:
                return "none_found"
            call.ctx["selected_slot"] = nxt
            call._emit_slot()
            return "slot_found"
        if call.backup_queue is None:
            primary = call.context["primary_slot"]
            call.backup_queue = [s for s in backups
                                 if (s["date"], s["band"]) != (primary["date"], primary["band"])
                                 and date.fromisoformat(s["date"]) > call.today]
        n = call.policies["max_options_per_turn"]
        call.offered, call.backup_queue = call.backup_queue[:n], call.backup_queue[n:]
        if not call.offered:
            return "no_backup_slots"
        call._report("get_backup_slots", {"offered": call.offered})
        return None

    # ------------------------------------------------------------ writes (mocked)
    def confirm_slot(self, call: "FlowCall", state: str):
        s = call.ctx.get("selected_slot") or {}
        call._report("confirm_slot", {"site_id": call.context["site_id"], **s,
                                      "readiness": call.ctx["readiness"],
                                      "preferred_exact_time": call.ctx.get("preferred_exact_time")})
        return None

    def site_on_hold(self, call: "FlowCall", state: str):
        call._report("site_on_hold", {"site_id": call.context["site_id"], "hold_reason": call.ctx.get("hold_reason"),
                                      "hold_until": call.ctx.get("hold_until"),
                                      "missing_items": call.ctx.get("missing_items"), "consent": True})
        return None

    def schedule_recall(self, call: "FlowCall", state: str):
        call._report("schedule_recall", {"site_id": call.context["site_id"],
                                         "recall_window": call.ctx.get("recall_window"),
                                         "fallback": "Beat Plan comment + scheduler task (recall API not built yet)"})
        return None

    def create_rm_callback(self, call: "FlowCall", state: str):
        call._report("create_rm_callback", {"site_id": call.context["site_id"],
                                            "reason": call.ctx.get("rm_callback_reason") or call.outcome or state,
                                            "preferred_exact_time": call.ctx.get("preferred_exact_time")})
        return None

    def request_date(self, call: "FlowCall", state: str):
        call._report("request_date", {"site_id": call.context["site_id"], "requested_date": call.ctx.get("requested_date"),
                                      "requested_band": call.ctx.get("requested_band")})
        return None

    def create_support_ticket(self, call: "FlowCall", state: str):
        t = call.ctx.get("support_ticket") or {"category": "complaint", "customer_words": "", "priority": "normal"}
        call._report("create_support_ticket", {"site_id": call.context["site_id"],
                                               "customer_id": call.context["customer"]["customer_id"],
                                               **t, "summary": call.summary()})
        return None

    def mark_dnc(self, call: "FlowCall", state: str):
        call._report("mark_dnc", {"customer_id": call.context["customer"]["customer_id"]})
        return None

    # ------------------------------------------------------------ post-call
    def summarise_call(self, call: "FlowCall", state: str):
        call.ctx["summary"] = call.summary()  # deterministic for now; LLM summary is a later step
        return None

    def post_beatplan_comment(self, call: "FlowCall", state: str):
        call._report("post_beatplan_comment", {"site_id": call.context.get("site_id"), "record": call.record()})
        return None
