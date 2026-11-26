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
    assert call.state == "RECAP" and "बजे" not in r.speak[0]          # no time window unless asked
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
    r = call.handle("deny")                          # no ladder
    assert "arrange" in r.speak[0] and call.state == "READINESS_CHECK"
    call.handle("deny")                              # can't arrange one either
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
    short = call._reask()
    assert short != question and len(short) < len(question)                       # the short form, not the whole offer again
    assert call.state == "OFFER_PRIMARY" and said in r.speak[0] and r.speak[0].endswith(short)
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


def test_a_missing_ladder_given_up_front_still_asks_if_they_can_arrange_one():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("affirm", {"readiness": {"ladder": False, "water": True}})    # no ladder, has water
    assert call.state == "READINESS_CHECK" and "arrange" in r.speak[-1]           # PR #4's arrange question first
    assert call.handle("repeat_request").speak == ["क्या आप ladder arrange कर पाएँगे?"]
    r = call.handle("deny")                                                       # cannot arrange one -> on to light
    assert call.state == "READINESS_CHECK" and "light" in r.speak[0]
    call.handle("affirm")                                                         # light
    assert call.state == "ASK_READY_DATE" and call.ctx["missing_items"] == ["ladder"]
    assert call.ctx["readiness"] == {"ladder": False, "water": True, "light": True}


def test_a_ladder_they_will_arrange_keeps_the_booking_when_given_up_front():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm", {"readiness": {"ladder": False, "water": True, "light": True}})
    r = call.handle("affirm")                                                     # "yes, I can arrange one"
    assert call.state == "RECAP" and call.ctx["will_arrange"] == ["ladder"] and not call.ctx["missing_items"]


def test_repeat_inside_the_arrange_question_repeats_that_question():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")                                                         # ladder question
    call.handle("deny")                                                           # -> "can you arrange one?"
    assert call.handle("repeat_request").speak == ["क्या आप ladder arrange कर पाएँगे?"]


def test_junk_readiness_entities_are_ignored():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("affirm", {"readiness": {"roof": True, "water": "maybe", "light": False}})
    assert call.ctx["known_readiness"] == {"light": False}      # "roof" and the non-boolean "water" are dropped
    assert call.ctx["readiness"] == {} and "ladder" in r.speak[-1].lower()  # the ladder is still asked


def _to_recap(call, steps=("affirm", "affirm", "affirm", "affirm", "affirm")):
    for on in steps:                               # greeting, offer, ladder, water, light
        call.handle(on)
    assert call.state == "RECAP"


def test_changing_the_time_at_the_recap_keeps_the_chosen_day_and_skips_the_checklist():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("deny")
    call.handle("affirm", {"option": 1})                         # picks Monday 30 Nov
    for _ in range(3):
        call.handle("affirm")                                    # ladder, water, light
    assert call.state == "RECAP" and call.ctx["selected_slot"]["date"] == "2026-11-30"
    call.handle("change_time")
    r = call.handle("affirm")                                    # "that window is fine"
    assert call.state == "RECAP" and "30 नवंबर" in r.speak[-1]
    assert "क्या आपके पास A-type ladder" not in r.speak[-1]                  # the checklist is not asked again
    assert call.ctx["selected_slot"]["date"] == "2026-11-30" and call.ctx["slot_source"] == "alt_day"
    call.handle("affirm")
    assert call.outcome == "slot_confirmed_alt_day"
    assert call.record()["confirmed_slot"]["date"] == "2026-11-30"


def test_changing_the_day_at_the_recap_does_not_ask_readiness_again():
    call, _, _ = live()
    _to_recap(call)
    call.handle("change_date")
    call.handle("affirm", {"option": 2})
    assert call.state == "RECAP" and call.ctx["selected_slot"]["date"] == "2026-12-03"


def test_no_at_the_recap_asks_what_to_change():
    call, _, _ = live()
    _to_recap(call)
    r = call.handle("deny")
    assert call.state == "RECAP_CHANGE" and "क्या बदलना है" in r.speak[0]
    assert call.handle("repeat_request").speak == ["दिन बदलना है या time?"]
    call.handle("change_time")
    assert call.state == "TIME_BAND"


def test_no_to_the_greeting_goes_to_wrong_person_and_a_plain_yes_continues():
    call, _, _ = live()
    r = call.handle("deny")
    assert call.state == "WRONG_PERSON" and not r.error
    call.handle("affirm")
    assert call.state == "OFFER_PRIMARY" and call.ctx["spoke_to"] == "family_member"


def test_time_requests_are_accepted_straight_at_the_offer():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("wants_other_band", {"band": "evening"})
    assert not r.error and call.state == "ALTERNATE_DAYS"                  # evening is full in the mock data

    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("wants_exact_time", {"exact_time": "10 baje"})
    assert call.state == "TIME_BAND" and "guarantee" in r.speak[0]
    call.handle("wants_exact_time", {"exact_time": "10 baje"})              # insists: escalates, as in TIME_BAND
    assert call.state == "RM_CLOSE" and call.outcome == "rm_callback"


@pytest.mark.parametrize("path", [
    ("affirm", "affirm", "affirm", "deny", "deny", "affirm"),     # ladder missing -> ASK_READY_DATE
])
def test_dont_know_when_the_site_will_be_ready_goes_to_a_senior(path):
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")                                         # ladder question
    call.handle("deny")                                           # no ladder -> can you arrange one?
    call.handle("deny")                                           # no -> water
    call.handle("affirm")                                         # water
    call.handle("affirm")                                         # light -> ASK_READY_DATE
    assert call.state == "ASK_READY_DATE"
    r = call.handle("dont_know")
    assert r.end and call.outcome == "rm_callback" and call.record()["rm_callback_reason"] == "customer_unsure"


def test_dont_know_in_site_on_hold_and_recall_goes_to_a_senior():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("not_serviceable")
    assert call.state == "SITE_ON_HOLD"
    assert call.handle("dont_know").end and call.record()["rm_callback_reason"] == "customer_unsure"

    call, _, _ = live()
    call.handle("wrong_person")
    call.handle("customer_unavailable")
    assert call.state == "RECALL"
    assert call.handle("dont_know").end and call.outcome == "rm_callback"


def test_dont_know_the_time_of_day_still_records_the_requested_date():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("proposes_date", {"date": "7 december"})              # not a Beat Plan day
    assert call.state == "ASK_BAND_FOR_DATE"
    r = call.handle("dont_know")
    assert r.end and call.outcome == "date_requested"


def test_yes_and_asks_the_time_in_one_turn_gets_the_time_answered():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("deny")                                                           # other days
    r = call.handle("affirm", {"option": 2, "asks_time": True})                   # "ok that one, but what time?"
    window = call._global_line("time_window")
    assert call.state == "READINESS_CHECK" and window and r.speak[0].startswith(window)
    assert "A-type ladder" in r.speak[-1]                                          # and the next question follows
    for _ in range(3):
        last = call.handle("affirm")
    assert call.state == "RECAP" and "के बीच" in last.speak[0]                    # the recap now names the window
    assert call.record()["time_window_told"] is True


def test_asks_the_time_at_the_ladder_question_is_answered_not_read_as_no():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")                                                         # ladder question
    ladder = call.last_line
    r = call.handle("ask_time")
    assert call.state == "READINESS_CHECK" and r.speak[0].startswith(call._global_line("time_window"))
    assert r.speak[0].endswith(call._reask()) and call.arranging is None          # same question, no "can you arrange?"

    r = call.handle("deny", {"asks_time": True})                                  # "no, and what time?"
    assert r.speak[0].startswith(call._global_line("time_window")) and "arrange" in r.speak[0]
    assert call.arranging == "ladder"


def test_asking_the_time_before_a_day_is_chosen_gives_the_service_hours():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("deny")
    options = call.last_line
    r = call.handle("ask_time")
    assert call.state == "ALTERNATE_DAYS"
    assert r.speak[0].startswith(call._global_line("service_hours")) and "सुबह 9 से शाम 6" in r.speak[0]
    assert r.speak[0].endswith(call._reask())


def test_ask_time_at_the_offer_still_moves_to_the_time_window_step():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("ask_time")
    assert call.state == "TIME_BAND" and "आएँगे" in r.speak[0]


def test_asks_time_flag_is_ignored_when_unset_and_not_doubled_in_the_time_step():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("affirm")                                                     # no asks_time: no prefix
    assert not r.speak[0].startswith(call._global_line("time_window"))
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("ask_time", {"asks_time": True})                              # TIME_BAND already tells the window
    assert call.state == "TIME_BAND" and r.speak[0].count("आएँगे") == 1


def test_asking_the_time_in_english():
    call, _, _ = live(lang="en")
    call.handle("affirm")
    call.handle("affirm")
    r = call.handle("ask_time")
    assert r.speak[0].startswith("Our service hero will come between")


@pytest.mark.parametrize("second_reply", ["deny", "wrong_person", "wrong_number"])
def test_not_the_person_then_any_kind_of_no_ends_as_a_wrong_number(second_reply):
    """The reported call: 'main Sneha nahi hoon' -> 'are you from her home?' -> 'nahi' / 'wrong number hai'."""
    call, _, _ = live()
    r = call.handle("wrong_person")
    assert call.state == "WRONG_PERSON" and "के घर से" in r.speak[0]
    r = call.handle(second_reply)
    assert r.end and call.outcome == "wrong_number" and call.state == "WRONG_NUMBER_CLOSE"


def test_wrong_number_closes_from_any_step():
    for steps in ([], ["affirm"], ["affirm", "affirm"]):
        call, _, _ = live()
        for on in steps:
            call.handle(on)
        r = call.handle("wrong_number")
        assert r.end and call.outcome == "wrong_number"


def test_a_global_intent_that_would_reenter_the_step_counts_as_unclear(monkeypatch):
    monkeypatch.setitem(SPEC.states["WRONG_PERSON"], "disable_global", [])      # as before the fix
    call, _, _ = live()
    call.handle("wrong_person")
    question = call.last_line
    r = call.handle("wrong_person")                                             # used to repeat the question forever
    assert call.state == "WRONG_PERSON" and r.speak[0].endswith(call._reask()) and call.unclear_streak == 1
    call.handle("wrong_person")
    r = call.handle("wrong_person")
    assert r.end and call.outcome == "rm_callback"                               # escalates instead of looping


def test_the_classifier_prompt_carries_the_examples_for_this_case():
    from agent.prompts import flow_system_prompt

    call, _, _ = live()
    prompt = flow_system_prompt(call)
    assert "wrong_number" in prompt and "wrong number है" in prompt and "मैं Sneha नहीं हूँ" in prompt
    assert "bare \"nahi\"" in prompt


# ---- fixes from three sample calls (SSE-15557)
@pytest.mark.parametrize("text,band", [
    ("सुबह", "morning"), ("सुबह।", "morning"), ("शाम", "evening"), ("दोपहर", "afternoon"),
    ("सवेरे", "morning"), ("सुबह 9:00 बजे", "morning"), ("शाम 5 बजे", "evening"), ("subah", "morning"),
])
def test_devanagari_times_of_day_are_understood(text, band):
    assert parse_band(text, SPEC.policies) == band


def _to_band_question(call):
    call.handle("affirm")
    call.handle("proposes_date", {"date": "7 december"})          # a day Beat Plan does not have
    assert call.state == "ASK_BAND_FOR_DATE"


def test_morning_in_devanagari_answers_the_band_question():
    call, _, _ = live()
    _to_band_question(call)
    r = call.handle("gives_band", {"band": "सुबह"})
    assert r.end and call.outcome == "date_requested" and call.record()["requested_slot"]["band"] == "morning"


def test_a_clock_time_outside_service_hours_gets_the_hours_then_the_question():
    call, _, _ = live()
    _to_band_question(call)
    for said in ("सुबह 7:00 बजे", "सुबह 8:00 बजे"):               # the reported call
        r = call.handle("gives_band", {"band": said})
        assert call.state == "ASK_BAND_FOR_DATE" and not r.end
        assert "9 से शाम 6" in r.speak[0] and r.speak[0].endswith("सुबह, दोपहर या शाम, कौन सा time ठीक रहेगा?")
    r = call.handle("gives_band", {"band": "सुबह"})
    assert r.end and call.outcome == "date_requested"


def test_re_asks_use_the_short_form_and_never_repeat_the_recording_disclosure():
    call, first, _ = live()
    assert "record" in first.speak[0]                             # said once, at the start
    r = call.handle("unclear")                                    # the reported "सुबह।" at the greeting
    assert "record" not in r.speak[0] and r.speak[0].endswith("क्या मैं Rahul जी से बात कर रहा हूँ?")
    call.handle("affirm")
    r = call.handle("price_or_offtopic")
    assert len(r.speak[0]) < 140 and "maintenance due" not in r.speak[0]


def test_an_ill_customer_gets_sympathy_and_is_asked_when_to_call_back():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("personal_reason", {"reason": "fever"})
    assert call.state == "RECALL" and "ध्यान रखिए" in r.speak[0] and "किस time" in r.speak[-1]
    assert "site" not in " ".join(r.speak)                         # not "when will your site be ready?"
    r = call.handle("personal_reason")                             # says it again inside RECALL: no loop, no unclear
    assert call.state == "RECALL" and call.unclear_streak == 0 and "किस time" in r.speak[0]
    call.handle("time_range_given", {"time_range": "kal shaam"})
    assert call.outcome == "recall_requested"


def test_a_complaint_gets_an_apology_and_a_senior_callback():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("complaint")
    assert r.end and call.outcome == "rm_callback" and call.record()["rm_callback_reason"] == "complaint"
    assert r.speak[0] == "माफ़ कीजिए, यह सुनकर बुरा लगा।" and "senior" in r.speak[-1]


def test_a_family_member_who_says_go_ahead_at_the_greeting_goes_straight_to_the_offer():
    call, _, _ = live()
    r = call.handle("household_member")
    assert call.state == "OFFER_PRIMARY" and call.ctx["spoke_to"] == "family_member" and "slot" in r.speak[0]


def test_another_language_gets_an_i_dont_understand_then_a_senior_after_two_more_turns():
    call, _, _ = live()
    r = call.handle("other_language")
    assert "I don't understand this language" in r.speak[0] and "हिंदी या English" in r.speak[0]
    assert r.speak == [call._global_line("language_not_supported")] and call.state == "GREET"   # one request, no 2nd question
    call.handle("other_language")
    r = call.handle("other_language")                              # third turn in a row
    assert r.end and call.outcome == "rm_callback" and call.record()["rm_callback_reason"] == "language_not_supported"


def test_speaking_hindi_again_resets_the_other_language_count():
    call, _, _ = live()
    call.handle("other_language")
    call.handle("other_language")
    call.handle("affirm")                                          # back in Hindi
    for _ in range(2):
        assert not call.handle("other_language").end               # counting starts again from zero


def test_small_talk_gets_a_friendly_line_and_the_question_again():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("small_talk")
    assert call.state == "OFFER_PRIMARY" and r.speak[0].startswith("जी, मैं ठीक हूँ, शुक्रिया।")
    assert r.speak[0].endswith(call._reask()) and call.unclear_streak == 0


def test_every_weekday_mentioned_is_read_and_sunday_is_skipped():
    from datetime import date as D
    from agent import normalize as N

    assert N.weekday_dates("agle somwar ya ravivar", D(2026, 11, 26)) == [D(2026, 11, 30), D(2026, 11, 29)]
    assert N.weekday_dates("रविवार या सोमवार", D(2026, 11, 26)) == [D(2026, 11, 29), D(2026, 11, 30)]
    call, _, _ = live()
    call.handle("affirm")
    call.handle("proposes_date", {"date": "nahi, mujhe ravivar ya somwar"})   # Sunday first, Monday second
    assert call.ctx["selected_slot"]["date"] == "2026-11-30"                   # Monday: a visit day and a free day


def test_a_sunday_only_request_gets_the_visit_days_and_the_question_again():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("proposes_date", {"date": "ravivar"})
    assert call.state == "OFFER_PRIMARY" and "सोमवार से शनिवार" in r.speak[0] and r.speak[0].endswith(call._reask())
    r = call.handle("proposes_date", {"date": "agle somwar ya ravivar"})      # the reported request
    assert call.state == "READINESS_CHECK" and call.ctx["selected_slot"]["date"] == "2026-11-30"


# ---- fourth sample call (SSE-15557)
def test_the_greeting_is_short_but_still_discloses_the_recording():
    call, first, _ = live()
    old_hindi = "नमस्ते, क्या मेरी बात Rahul जी से हो रही है? मैं Krupal, SolarSquare से बोल रहा हूँ। यह कॉल रिकॉर्ड की जा रही है।"
    assert len(first.speak[0]) < len(old_hindi) - 15                     # clearly shorter than before
    assert "record" in first.speak[0] and "SolarSquare" in first.speak[0] and "Rahul" in first.speak[0]
    call, first, _ = live(lang="en")
    assert "recorded" in first.speak[0] and "SolarSquare" in first.speak[0] and len(first.speak[0]) < 100


@pytest.mark.parametrize("item,intent,asked", [
    ("ladder", "unsure", "ladder arrange"), ("water", "unsure", "पानी का इंतज़ाम"), ("light", "unsure", "light का connection हो जाएगा"),
    ("ladder", "dont_know", "ladder arrange"),
])
def test_a_maybe_is_never_booked_as_ready_it_gets_one_clear_follow_up(item, intent, asked):
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")                                           # ladder question
    for before in {"ladder": [], "water": ["affirm"], "light": ["affirm", "affirm"]}[item]:
        call.handle(before)
    r = call.handle(intent)
    assert call.state == "READINESS_CHECK" and call.arranging == item and asked in r.speak[0]
    assert item not in call.ctx["readiness"]                        # not recorded as ready yet
    assert call.handle("repeat_request").speak[0].startswith("क्या ")   # the short form of that follow-up


def test_a_maybe_that_becomes_a_yes_is_recorded_as_will_arrange_and_the_recap_says_so():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")
    call.handle("unsure")                                           # ladder: "शायद"
    call.handle("affirm")                                           # "yes, I'll get one"
    call.handle("affirm")                                           # water
    r = call.handle("affirm")                                       # light -> recap
    assert call.state == "RECAP" and call.ctx["will_arrange"] == ["ladder"]
    assert "ladder का इंतज़ाम आप visit से पहले कर लेंगे" in r.speak[0] and "पानी और light ready हैं" in r.speak[0]
    assert "ladder, पानी" not in r.speak[0]                          # no longer claims the ladder is ready


def test_a_maybe_that_stays_a_maybe_counts_as_missing_and_leads_to_the_hold_flow():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")
    call.handle("unsure")                                           # "शायद"
    call.handle("unsure")                                           # "शायद" again to the follow-up
    call.handle("affirm")
    call.handle("affirm")
    assert call.state == "ASK_READY_DATE" and call.ctx["missing_items"] == ["ladder"]


@pytest.mark.parametrize("asked_at,words", [
    ([], "सीढ़ी"), (["affirm"], "पानी"), (["affirm", "affirm"], "बिजली"),
])
def test_what_does_that_mean_is_explained_not_answered_with_could_not_hear(asked_at, words):
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")
    for on in asked_at:
        call.handle(on)
    r = call.handle("asks_meaning")                                 # "ये A type ladder क्या होता है?"
    assert words in r.speak[0] and "सुनाई नहीं दिया" not in r.speak[0] and call.unclear_streak == 0
    assert r.speak[0].endswith(call._reask())                       # then the question again, short


def test_asking_what_something_means_outside_the_checklist_gets_the_deflection():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("asks_meaning")
    assert r.speak[0].startswith(call._global_line("deflection")) and call.state == "OFFER_PRIMARY"


def test_beat_plan_slots_on_sunday_are_never_offered():
    b = {"id": "BK-3", "sseId": "SSE-MUM-1", "customerName": "Amit Mehta", "phone": "+910000000003",
         "beatPlan": {"primary_slot": {"date": "2026-11-29", "band": "evening", "hero_id": "SH-14"},     # Sunday
                      "backup_slots": [{"date": "2026-12-01", "band": "evening", "hero_id": "SH-14"}],
                      "availability": {}}}
    call, first, _ = live(b)
    r = call.handle("affirm")
    assert call.context["primary_slot"]["date"] == "2026-12-01" and "मंगलवार" in r.speak[0]
    assert "रविवार" not in r.speak[0]


def test_a_call_whose_every_slot_is_a_sunday_is_not_placed():
    b = {"id": "BK-3", "sseId": "SSE-MUM-1", "customerName": "Amit Mehta", "phone": "+910000000003",
         "beatPlan": {"primary_slot": {"date": "2026-11-29", "band": "evening", "hero_id": "SH-14"},
                      "backup_slots": [], "availability": {}}}
    call, first, _ = live(b)
    assert first.end and call.outcome == "not_dialled" and call.ctx["blocked_reason"] == "no_visit_day_slot"


def test_confirming_a_sunday_is_refused_so_the_team_confirms_by_hand():
    from agent.beatplan import MockBeatPlan

    call, _, _ = live()
    call.ctx["selected_slot"] = {"date": "2026-11-29", "band": "morning", "hero_id": "SH-7"}
    assert MockBeatPlan().confirm_slot(call, "BOOK") == "action_failed"
    call.ctx["selected_slot"] = {"date": "2026-11-30", "band": "morning", "hero_id": "SH-7"}
    assert MockBeatPlan().confirm_slot(call, "BOOK") is None


def test_a_family_member_is_spoken_to_as_family_not_as_the_customer():
    call, _, _ = live()
    r = call.handle("household_member")
    assert "Rahul जी के solar system" in r.speak[0] and "आपके solar system" not in r.speak[0]
    assert "घर पर कोई available" in r.speak[0]
    assert call.handle("price_or_offtopic").speak[0].endswith("घर पर कोई available रहेगा?")      # short form too
    for _ in range(5):                                              # offer, ladder, water, light, recap
        last = call.handle("affirm")
    assert call.outcome == "slot_confirmed" and "Rahul जी की visit" in last.speak[-1] and "आपकी visit" not in last.speak[-1]


def test_the_customer_himself_still_hears_your():
    call, _, _ = live()
    r = call.handle("affirm")
    assert "आपके solar system" in r.speak[0]


# ---- fifth sample call (SSE-15557)
@pytest.mark.parametrize("text,days", [
    ("1 महीने बाद", 30), ("एक महीने बाद", 30), ("after 1 month", 30), ("I am saying it will be ready after 1 month.", 30),
    ("15 दिन बाद", 15), ("दो हफ़्ते बाद", 14), ("10 din baad", 10), ("अगले महीने", 30), ("next week", 7), ("3 din", 3),
])
def test_relative_durations_become_dates(text, days):
    from datetime import timedelta
    from agent import normalize as N

    assert N.parse_date(text, TODAY) == TODAY + timedelta(days=days)


def test_a_month_later_answers_when_will_the_site_be_ready():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("not_serviceable", {"reason": "gaon ja raha hoon"})                # "मैं गाँव जा रहा हूँ"
    assert call.state == "SITE_ON_HOLD"
    call.handle("date_given", {"date": "1 महीने बाद"})
    assert call.state == "HOLD_CONSENT" and call.ctx["hold_until"] == "2026-12-26"


def test_unusable_answers_escalate_instead_of_asking_forever():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("not_serviceable", {"reason": "gaon"})
    r = call.handle("date_given", {"date": "pata nahi kab"})                       # heard, but no date in it
    assert not r.end and "5 दिसंबर" in r.speak[0] and call.unclear_streak == 1
    call.handle("date_given", {"date": "abhi nahi"})
    r = call.handle("date_given", {"date": "kuch bhi"})
    assert r.end and call.outcome == "rm_callback" and call.record()["rm_callback_reason"] == "repeated_misunderstanding"


def test_a_good_answer_resets_the_failed_answer_count():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("not_serviceable", {"reason": "gaon"})
    call.handle("date_given", {"date": "kuch bhi"})
    assert call.unclear_streak == 1
    call.handle("date_given", {"date": "5 december"})
    assert call.state == "HOLD_CONSENT" and call.unclear_streak == 0


def test_yes_with_a_hedge_is_a_maybe_not_a_yes():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")                                                          # ladder question
    r = call.handle("affirm", {"hedged": True})                                    # "जी हाँ, फिर तो शायद है"
    assert call.arranging == "ladder" and "arrange" in r.speak[0] and "ladder" not in call.ctx["readiness"]


def test_the_recap_is_a_proper_sentence():
    call, _, _ = live()
    _to_recap(call)
    assert "ladder, पानी और light ready हैं" in call.last_line and "और ladder" not in call.last_line

    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")
    call.handle("affirm")                                                          # ladder yes
    call.handle("affirm")                                                          # water yes
    call.handle("unsure")                                                          # light: "शायद है"
    r = call.handle("affirm")                                                      # "yes, I'll arrange it"
    assert call.state == "RECAP"
    assert "ladder और पानी ready हैं, light का इंतज़ाम आप visit से पहले कर लेंगे" in r.speak[0]
    assert "रहेंगे और light" not in r.speak[0]
    call, _, _ = live(lang="en")
    _to_recap(call)
    assert "Ladder, water and light will be ready" in call.last_line


def test_a_readiness_answer_can_be_corrected_at_the_recap():
    call, _, _ = live()
    _to_recap(call)
    r = call.handle("corrects_readiness", {"readiness": {"ladder": False}})        # "मेरे पास ladder नहीं है"
    assert call.state == "READINESS_CHECK" and call.arranging == "ladder" and "arrange" in r.speak[-1]
    call.handle("deny")                                                            # cannot arrange one
    assert call.state == "ASK_READY_DATE" and call.ctx["missing_items"] == ["ladder"]
    assert call.ctx["readiness"] == {"ladder": False, "water": True, "light": True}


def test_a_promised_item_corrected_to_missing_is_asked_again_from_the_change_step():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")
    call.handle("unsure")
    call.handle("affirm")                                                          # ladder: will arrange
    call.handle("affirm")
    call.handle("affirm")
    assert call.state == "RECAP" and call.ctx["will_arrange"] == ["ladder"]
    call.handle("deny")                                                            # "no" -> what to change?
    assert call.state == "RECAP_CHANGE"
    r = call.handle("corrects_readiness", {"readiness": {"ladder": False}})        # the reported moment
    assert call.arranging == "ladder" and call.ctx["will_arrange"] == []


def test_correcting_a_water_answer_goes_to_the_hold_flow_and_a_good_correction_returns_to_the_recap():
    call, _, _ = live()
    _to_recap(call)
    call.handle("corrects_readiness", {"readiness": {"water": False}})
    assert call.state == "ASK_READY_DATE" and call.ctx["missing_items"] == ["water"]
    call, _, _ = live()
    _to_recap(call)
    call.handle("corrects_readiness", {"readiness": {"light": True}})              # nothing actually changes
    assert call.state == "RECAP"


def test_a_correction_with_no_usable_item_is_not_understood():
    call, _, _ = live()
    _to_recap(call)
    r = call.handle("corrects_readiness", {"readiness": {"roof": False}})
    assert call.state == "RECAP" and call.unclear_streak == 1 and not r.end


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


def test_failed_callback_save_does_not_promise_a_callback():
    b = {**BOOKING, "beatPlan": {**BOOKING["beatPlan"], "fail_actions": ["schedule_recall"]}}
    call, _, events = live(b)
    call.handle("wrong_person")
    call.handle("customer_unavailable")
    r = call.handle("time_range_given", {"time_range": "kal shaam 5 se 7"})
    assert call.outcome == "needs_manual_confirmation" and r.end
    assert "call करेंगे" not in r.speak[-1] and "team जल्द ही confirm" in r.speak[-1]


def test_failed_senior_callback_does_not_promise_one():
    b = {**BOOKING, "beatPlan": {**BOOKING["beatPlan"], "fail_actions": ["create_rm_callback"]}}
    call, _, _ = live(b)
    call.handle("affirm")
    r = call.handle("wants_human")
    assert call.outcome == "needs_manual_confirmation" and r.end
    assert "senior" not in r.speak[-1] and "team जल्द ही confirm" in r.speak[-1]


def test_working_saves_still_promise_the_callback():
    call, _, _ = live()
    call.handle("wrong_person")
    call.handle("customer_unavailable")
    call.handle("time_range_given", {"time_range": "kal shaam 5 se 7"})
    assert call.outcome == "recall_requested"
    call, _, _ = live()
    call.handle("affirm")
    call.handle("wants_human")
    assert call.outcome == "rm_callback"


def test_english_switch():
    call, _, _ = live()
    r = call.handle("affirm", {"language": "en"})
    assert "maintenance is due" in r.speak[0] and "Saturday" in r.speak[0]


def test_booking_without_beatplan_block_falls_back_to_date_and_window():
    call, first, _ = live({"id": "BK-9", "customerName": "Amit Mehta", "date": "2026-11-30", "window": "15-18"})
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


# ---------- changes from the call samples (v0.3.0)

def test_ladder_can_be_arranged_keeps_booking():
    call, _, events = live()
    call.handle("affirm")
    call.handle("affirm")
    call.handle("deny")                              # no ladder now
    r = call.handle("affirm")                        # but can arrange one
    assert "पानी" in r.speak[0]                      # moves on to water
    call.handle("affirm")
    call.handle("affirm")
    assert call.state == "RECAP"
    call.handle("affirm")
    rec = call.record()
    assert call.outcome == "slot_confirmed" and rec["will_arrange"] == ["ladder"] and rec["readiness"]["ladder"] is True
    assert any(e.get("display") == "will arrange before the visit" for e in events if e["type"] == "slot")


def test_not_needed_yet_gets_one_explanation():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("not_needed_now")
    assert call.state == "NOT_NEEDED" and "check-up" in r.speak[0]
    assert "not_needed_now" not in r.allowed      # only one try
    r = call.handle("deny")
    assert call.outcome == "not_needed_now" and r.end


def test_not_needed_yet_then_agrees():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("not_needed_now")
    call.handle("affirm")
    assert call.state == "READINESS_CHECK"


def test_time_outside_service_hours_is_explained():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("ask_time")
    r = call.handle("outside_service_hours")
    assert call.state == "TIME_BAND" and "9 से शाम 6" in r.speak[0] and "के बीच hero आएँगे" in r.speak[0]


def test_bands_are_9_to_12_12_to_3_3_to_6():
    from agent.flow import speak_band
    assert speak_band("morning", SPEC.policies, "en") == "9 am and 12 pm"
    assert speak_band("afternoon", SPEC.policies, "en") == "12 pm and 3 pm"
    assert speak_band("evening", SPEC.policies, "en") == "3 pm and 6 pm"


def test_own_date_that_beat_plan_has_is_booked():
    call, _, events = live()
    call.handle("affirm")
    call.handle("proposes_date", {"date": "3 december"})   # a backup day
    assert call.state == "READINESS_CHECK"
    for _ in range(4):
        call.handle("affirm")
    assert call.outcome == "slot_confirmed_alt_day"
    assert beatplan(events, "confirm_slot")[0]["date"] == "2026-12-03"


def test_own_date_not_in_beat_plan_is_a_request():
    call, _, events = live()
    call.handle("affirm")
    r = call.handle("proposes_date", {"date": "10 december"})
    assert call.state == "ASK_BAND_FOR_DATE" and "10 दिसंबर" in r.speak[0]
    r = call.handle("gives_band", {"band": "subah"})
    assert call.outcome == "date_requested" and "confirm" in r.speak[0]
    assert beatplan(events, "request_date")[0] == {"site_id": "SSE-PUN-1", "requested_date": "2026-12-10",
                                                  "requested_band": "morning"}
    assert call.record()["requested_slot"] == {"date": "2026-12-10", "band": "morning"}
    assert not beatplan(events, "confirm_slot")


def test_sms_line_only_when_policy_on():
    call, _, _ = live()
    for _ in range(5):
        call.handle("affirm")
    r = call.handle("affirm")
    assert "SMS" not in r.speak[0]
    spec = FlowSpec.load()
    spec.policies["sms_reminder"] = True
    call = FlowCall(call_id="S", booking=BOOKING, spec=spec, today=TODAY)
    call.start()
    for _ in range(5):
        call.handle("affirm")
    r = call.handle("affirm")
    assert "SMS" in r.speak[0] and call.outcome == "slot_confirmed"


# ---------- time window only when the customer asks (v0.3.0)

def test_plain_yes_never_mentions_a_time_window():
    call, _, events = live()
    lines = []
    for _ in range(6):
        lines += call.handle("affirm").speak
    assert call.outcome == "slot_confirmed"
    assert not any("बजे" in ln or "सुबह" in ln for ln in lines), lines
    assert call.record()["time_window_told"] is False
    assert call.record()["confirmed_slot"]["band"] == "morning"     # Beat Plan still gets the band


def test_asking_the_time_shares_it_in_recap_and_close():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("ask_time")
    assert "सुबह नौ से बारह बजे" in r.speak[0]
    for _ in range(3):
        call.handle("affirm")                                        # window ok, ladder, water
    r = call.handle("affirm")                                        # light -> recap
    assert "सुबह नौ से बारह बजे" in r.speak[0]
    r = call.handle("affirm")
    assert "सुबह नौ से बारह बजे" in r.speak[-1] and call.record()["time_window_told"] is True


def test_backup_days_offered_without_time_of_day_unless_asked():
    call, _, _ = live()
    call.handle("affirm")
    r = call.handle("deny")
    assert "सोमवार" in r.speak[0] and "सुबह" not in r.speak[0] and "शाम" not in r.speak[0]
