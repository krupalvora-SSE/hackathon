"""The YAML flow (flows/service-scheduling) running inside the bot.

1. Paper mode: every scenario in scenarios.yaml through the real FlowCall engine.
2. Live mode: real actions against MockBeatPlan with booking data, checking lines,
   outcomes, Beat Plan payloads and the call record.
"""
from datetime import date

import pytest

from agent.flow import FlowCall, FlowSpec, _load, parse_band
from agent.tools import Action

TODAY = date(2026, 11, 26)  # Thursday
SPEC = FlowSpec.load()


def scenarios():
    return _load(SPEC.dir / "scenarios.yaml")


@pytest.mark.parametrize("sc", scenarios(), ids=lambda sc: sc["name"])
def test_scenarios_paper_mode(sc):
    call = FlowCall(call_id="P", booking={}, spec=SPEC, today=TODAY, auto=False)
    call.state = SPEC.flow["start"]
    for step in sc["steps"]:
        on, flags = (step, {}) if isinstance(step, str) else (step["on"], step.get("flags", {}))
        r = call.handle(on, flags=flags)
        assert r.error is None, f"{on} in {call.state}: {r.error}"
    assert call.outcome == sc["expect"], " > ".join(call.path)


BOOKING = {
    "id": "BK-1", "sseId": "SSE-PUN-1", "customerName": "Rahul Joshi", "phone": "+910000000001",
    "beatPlan": {
        "primary_slot": {"date": "2026-11-28", "band": "morning", "hero_id": "SH-7"},
        "backup_slots": [
            {"date": "2026-12-03", "band": "evening", "hero_id": "SH-7"},
            {"date": "2026-11-30", "band": "morning", "hero_id": "SH-2"},
            {"date": "2026-12-05", "band": "morning", "hero_id": "SH-7"},
        ],
        "availability": {"2026-11-28": ["morning", "afternoon"]},
    },
}


def live(booking=BOOKING, lang="hi"):
    events = []
    call = FlowCall(call_id="L", booking=booking, spec=SPEC, today=TODAY, on_event=events.append)
    call.lang = lang
    first = call.start()
    return call, first, events


def beatplan(events, action):
    return [e["payload"] for e in events if e["type"] == "beatplan" and e["action"] == action]


def test_greeting_and_primary_offer():
    call, first, _ = live()
    assert call.state == "GREET" and "Rahul" in first.speak[0] and "SolarSquare" in first.speak[0]
    r = call.handle("affirm")
    assert call.state == "OFFER_PRIMARY" and "शनिवार" in r.speak[0] and "28 नवंबर" in r.speak[0]
    assert "ask_time" in r.allowed and "not_serviceable" in r.allowed


def test_happy_path_checks_every_readiness_item_then_confirms():
    call, _, events = live()
    call.handle("affirm")
    r = call.handle("affirm")                       # yes to primary slot
    assert call.state == "READINESS_CHECK" and "ladder" in r.speak[-1]
    assert "पानी" in call.handle("affirm").speak[0]  # ladder yes -> water
    assert "light" in call.handle("affirm").speak[0]  # water yes -> light
    r = call.handle("affirm")
    assert call.state == "RECAP" and "सुबह आठ से बारह बजे" in r.speak[0]
    r = call.handle("affirm")
    assert r.end and call.outcome == "slot_confirmed"
    sent = beatplan(events, "confirm_slot")[0]
    assert sent["date"] == "2026-11-28" and sent["band"] == "morning" and sent["readiness"] == {
        "ladder": True, "water": True, "light": True}
    rec = call.record()
    assert rec["confirmed_slot"] == {"date": "2026-11-28", "band": "morning", "hero_id": "SH-7"}
    assert rec["slots"]["light_available"] is True and rec["flow_version"] == SPEC.flow["version"]
    assert beatplan(events, "post_beatplan_comment"), "every call ends with a Beat Plan comment"


def test_exact_time_once_is_noted_twice_escalates():
    call, _, events = live()
    call.handle("affirm")
    call.handle("ask_time")
    r = call.handle("wants_exact_time", {"exact_time": "10 baje"})
    assert call.state == "TIME_BAND" and "guarantee" in r.speak[0]
    r = call.handle("wants_exact_time", {"exact_time": "theek 10 baje"})
    assert r.end and call.outcome == "rm_callback"
    assert beatplan(events, "create_rm_callback")[0]["reason"] == "insists_on_exact_time"
    assert call.record()["preferred_exact_time"] == "theek 10 baje"


def test_other_band_free_same_day():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("ask_time")
    call.handle("wants_other_band", {"band": "dopahar"})
    assert call.state == "READINESS_CHECK"
    for _ in range(4):
        r = call.handle("affirm")
    assert call.outcome == "slot_confirmed_alt_band"
    assert call.record()["confirmed_slot"]["band"] == "afternoon"


def test_other_band_full_offers_closest_backups_two_at_a_time():
    call, _, events = live()
    call.handle("affirm")
    call.handle("ask_time")
    r = call.handle("wants_other_band", {"band": "shaam"})
    assert call.state == "ALTERNATE_DAYS"
    assert [s["date"] for s in call.offered] == ["2026-11-30", "2026-12-03"]  # closest first, max 2
    assert "सोमवार" in r.speak[0] and " या " in r.speak[0]
    call.handle("affirm", {"option": 2})
    for _ in range(4):
        call.handle("affirm")
    assert call.outcome == "slot_confirmed_alt_day"
    assert beatplan(events, "confirm_slot")[0]["date"] == "2026-12-03"


def test_no_alternate_fits():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("deny")
    assert len(call.offered) == 2
    call.handle("deny")
    assert [s["date"] for s in call.offered] == ["2026-12-05"]
    r = call.handle("deny")
    assert r.end and call.outcome == "no_slot_available"


def test_missing_ladder_puts_site_on_hold_with_consent():
    call, _, events = live()
    call.handle("affirm")
    call.handle("affirm")
    call.handle("deny")                              # no ladder
    call.handle("affirm")                            # water ok
    r = call.handle("affirm")                        # light ok
    assert call.state == "ASK_READY_DATE" and "A-type ladder" in r.speak[0]
    r = call.handle("date_given", {"date": "5 december"})
    assert call.state == "HOLD_CONSENT" and "call नहीं आएगी" in r.speak[0]
    r = call.handle("affirm")
    assert call.outcome == "site_on_hold"
    hold = beatplan(events, "site_on_hold")[0]
    assert hold["hold_until"] == "2026-12-05" and hold["missing_items"] == ["ladder"]
    assert call.record()["hold"]["consent"] is True


def test_out_of_town_with_date_skips_question_and_can_decline():
    call, _, events = live()
    call.handle("affirm")
    r = call.handle("not_serviceable", {"reason": "out of town", "date": "15 december"})
    assert call.state == "HOLD_CONSENT" and "15 दिसंबर" in r.speak[0]
    r = call.handle("deny")
    assert call.outcome == "hold_declined" and not beatplan(events, "site_on_hold")


def test_call_later_uses_recall_stub():
    call, _, events = live()
    call.handle("affirm")
    call.handle("call_later")
    r = call.handle("time_range_given", {"time_range": "kal shaam 5 se 7"})
    assert call.outcome == "recall_requested" and "kal shaam 5 se 7" in r.speak[0]
    assert "fallback" in beatplan(events, "schedule_recall")[0]


def test_price_question_deflects_and_repeats_question():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("price_or_offtopic")
    assert call.state == "OFFER_PRIMARY" and "टीम" in r.speak[0] and "available" in r.speak[0]


def test_unclear_three_times_escalates():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("unclear")
    call.handle("unclear")
    r = call.handle("unclear")
    assert r.end and call.outcome == "rm_callback"
    assert call.record()["rm_callback_reason"] == "repeated_misunderstanding"


def test_silence_reprompts_then_no_answer():
    call, _, _ = live()
    call.handle("affirm")
    assert "सुन" in call.silence().speak[0]
    call.silence()
    r = call.silence()
    assert r.end and call.outcome == "no_answer"


def test_intent_not_valid_here_is_rejected():
    call, _, _ = live()
    r = call.handle("wants_exact_time")
    assert r.error and call.state == "GREET" and "affirm" in r.allowed


def test_dnc_customer_is_never_dialled():
    call, first, events = live({**BOOKING, "dnc": True})
    assert first.end and first.speak == [] and call.outcome == "not_dialled"
    assert beatplan(events, "post_beatplan_comment")


def test_confirm_api_failure_needs_manual_confirmation():
    b = {**BOOKING, "beatPlan": {**BOOKING["beatPlan"], "fail_actions": ["confirm_slot"]}}
    call, _, events = live(b)
    for _ in range(6):
        call.handle("affirm")
    assert call.outcome == "needs_manual_confirmation" and beatplan(events, "create_rm_callback")


def test_english_switch():
    call, _, _ = live()
    r = call.handle("affirm", {"language": "en"})
    assert "maintenance is due" in r.speak[0] and "Saturday" in r.speak[0]


def test_booking_without_beatplan_block_falls_back_to_date_and_window():
    call, first, _ = live({"id": "BK-9", "customerName": "Amit Mehta", "date": "2026-11-29", "window": "15-18"})
    assert call.context["primary_slot"]["band"] == "evening"


@pytest.mark.parametrize("text,band", [("subah", "morning"), ("dopahar", "afternoon"), ("shaam ko", "evening"),
                                       ("4 baje", "evening"), ("10 baje", "morning"), ("evening", "evening"),
                                       ("pata nahi", None)])
def test_parse_band(text, band):
    assert parse_band(text, SPEC.policies) == band


def test_flow_engine_tool_dispatch():
    from agent.engine import FlowEngine
    events = []
    eng = FlowEngine("E1", BOOKING, on_event=events.append, today=TODAY)
    assert eng.opening() and "Rahul" in eng.opening()[0]
    a = eng.dispatch("customer_said", {"intent": "affirm"})
    assert isinstance(a, Action) and a.speak and a.run_llm is False and a.result["state"] == "OFFER_PRIMARY"
    bad = eng.dispatch("customer_said", {"intent": "nonsense"})
    assert bad.result["ok"] is False and bad.run_llm is True and "allowed_intents" in bad.result
    assert eng.dispatch("save_slot", {}).result["ok"] is False
