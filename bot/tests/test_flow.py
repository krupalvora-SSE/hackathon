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


def test_repeat_request_repeats_the_question_shorter_and_stays():
    call, first, _ = live()
    r = call.handle("repeat_request")                      # GREET
    assert call.state == "GREET" and r.speak == ["जी, क्या मैं Rahul जी से बात कर रहा हूँ?"]
    assert len(r.speak[0]) < len(first.speak[0])
    call.handle("affirm")
    full = call.last_line
    r = call.handle("repeat_request")                      # OFFER_PRIMARY
    assert call.state == "OFFER_PRIMARY" and len(r.speak[0]) < len(full) and "28 नवंबर" in r.speak[0]
    assert "सुनाई नहीं" not in r.speak[0] and "कट गई" not in r.speak[0]


def test_repeat_request_on_each_readiness_question():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")                                  # -> ladder question
    assert call.handle("repeat_request").speak == ["आपके पास A-type ladder है?"]
    call.handle("affirm")                                  # -> water question
    assert call.handle("repeat_request").speak == ["Panels के पास पानी मिल जाएगा?"]
    r = call.handle("affirm")                              # still advances normally
    assert "light" in r.speak[0]


def test_repeat_request_english_and_not_counted_as_unclear():
    call, _, _ = live(lang="en")
    call.handle("affirm")
    for _ in range(4):                                     # would escalate if counted as unclear
        r = call.handle("repeat_request")
    assert not r.end and r.speak == ["Are you free on Saturday, 28 November?"]


def test_repeat_request_without_short_form_falls_back_to_lead_in_plus_full_line():
    call, _, _ = live()
    call.handle("affirm")
    call.last_ref = ("OFFER_PRIMARY", "no_such_key")
    r = call.handle("repeat_request")
    assert r.speak[0].startswith("जी, फिर से बता देता हूँ।") and r.speak[0].endswith(call.last_line)


def test_hold_on_acknowledges_alone_and_asks_for_a_longer_silence_timeout():
    call, _, _ = live()
    call.handle("affirm")
    full = call.last_line
    r = call.handle("wait_a_moment")
    assert call.state == "OFFER_PRIMARY" and r.speak == ["जी, मैं line पर हूँ, आप देख लीजिए।"]
    assert full not in r.speak[0]                      # the question is not repeated
    assert r.idle_timeout == 25.0 and r.end is False
    assert call.handle("price_or_offtopic").idle_timeout is None


def test_hold_on_english_and_never_counts_as_unclear():
    call, _, _ = live(lang="en")
    call.handle("affirm")
    for _ in range(4):
        r = call.handle("wait_a_moment")
    assert not r.end and r.speak == ["Sure, I'm on the line, take your time."]


def test_hold_on_reaches_the_engine_action():
    from agent.engine import FlowEngine

    e = FlowEngine("H", BOOKING)
    e.dispatch("customer_said", {"intent": "affirm"})
    a = e.dispatch("customer_said", {"intent": "wait_a_moment"})
    assert a.idle_timeout == 25.0 and a.speak == ["जी, मैं line पर हूँ, आप देख लीजिए।"]


@pytest.mark.parametrize("intent,lang,said", [
    ("who_are_you", "hi", "SolarSquare"), ("who_are_you", "en", "SolarSquare"),
    ("number_source", "hi", "records"), ("number_source", "en", "records"),
    ("is_robot", "hi", "AI"), ("is_robot", "en", "AI"),
])
def test_identity_questions_get_an_honest_answer_then_the_question_again(intent, lang, said):
    call, _, _ = live(lang=lang)
    call.handle("affirm")
    question = call.last_line
    r = call.handle(intent)
    assert call.state == "OFFER_PRIMARY" and said in r.speak[0] and r.speak[0].endswith(question)
    assert "₹" not in r.speak[0] and "rupee" not in r.speak[0].lower()


def test_identity_questions_never_count_as_unclear():
    call, _, _ = live()
    call.handle("affirm")
    for intent in ("who_are_you", "number_source", "is_robot", "who_are_you"):
        r = call.handle(intent)
    assert not r.end


def test_several_answers_at_once_skip_the_questions_already_answered():
    call, _, events = live()
    call.handle("affirm")
    r = call.handle("affirm", {"readiness": {"ladder": True, "water": True}})     # yes + ladder + water
    assert call.state == "READINESS_CHECK" and "light" in r.speak[-1] and "ladder" not in r.speak[-1].lower()
    assert call.ctx["readiness"] == {"ladder": True, "water": True}
    r = call.handle("affirm")                                                     # light -> recap
    assert call.state == "RECAP"
    assert [e["key"] for e in events if e["type"] == "slot" and e["key"].endswith("_available")] == [
        "ladder_available", "water_source_available", "light_available"]


def test_all_three_answered_at_once_goes_straight_to_the_recap():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("affirm", {"readiness": {"ladder": True, "water": True, "light": True}})
    assert call.state == "RECAP" and "confirm" in r.speak[-1]
    assert not call.ctx["missing_items"]


def test_answers_given_mid_checklist_skip_the_following_questions():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")                                                         # asked about the ladder
    r = call.handle("affirm", {"readiness": {"water": True}})                     # "yes, and water too"
    assert call.state == "READINESS_CHECK" and "light" in r.speak[0]
    assert call.ctx["readiness"] == {"ladder": True, "water": True}


def test_a_missing_item_given_up_front_still_leads_to_the_hold_flow():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm", {"readiness": {"ladder": False, "water": True}})        # no ladder, has water
    r = call.handle("affirm")                                                     # light
    assert call.state == "ASK_READY_DATE" and call.ctx["missing_items"] == ["ladder"]
    assert call.ctx["readiness"] == {"ladder": False, "water": True, "light": True}


def test_junk_readiness_entities_are_ignored():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("affirm", {"readiness": {"roof": True, "water": "maybe", "light": False}})
    assert call.ctx["known_readiness"] == {"light": False}      # "roof" and the non-boolean "water" are dropped
    assert call.ctx["readiness"] == {} and "ladder" in r.speak[-1].lower()  # the ladder is still asked


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
