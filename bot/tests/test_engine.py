from datetime import date

import pytest

from agent import normalize as N
from agent.slots import CallState
from agent.tools import dispatch, on_silence

TODAY = date(2026, 11, 26)  # Thursday


@pytest.mark.parametrize("text,expected", [
    ("kal", date(2026, 11, 27)),
    ("parso", date(2026, 11, 28)),
    ("कल", date(2026, 11, 27)),
    ("udya", date(2026, 11, 27)),
    ("somvar", date(2026, 11, 30)),
    ("agle somvar", date(2026, 11, 30)),
    ("agle guruvar", date(2026, 12, 3)),
    ("shanivar ko", date(2026, 11, 28)),
    ("5 tarikh", date(2026, 12, 5)),
    ("28 tarikh", date(2026, 11, 28)),
    ("5 december", date(2026, 12, 5)),
    ("december 5", date(2026, 12, 5)),
    ("2026-12-01", date(2026, 12, 1)),
    ("1/12", date(2026, 12, 1)),
    ("paanch tarikh", date(2026, 12, 5)),
    ("pata nahi", None),
])
def test_parse_date(text, expected):
    assert N.parse_date(text, TODAY) == expected


@pytest.mark.parametrize("text,expected", [
    ("subah", ("09-12", None)),
    ("सुबह", ("09-12", None)),
    ("dopahar", ("12-15", None)),
    ("shaam", ("15-18", None)),
    ("3 baje", ("15-18", None)),
    ("4 pm", ("15-18", None)),
    ("11 am", ("09-12", None)),
    ("subah 10 baje", ("09-12", None)),
    ("12-15", ("12-15", None)),
    ("raat ko", (None, "out_of_hours")),
    ("shaam 7 baje", (None, "out_of_hours")),
    ("jab marzi", (None, "unclear")),
])
def test_parse_slot(text, expected):
    assert N.parse_time_slot(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("haan", True), ("haan na", True), ("ji haan hai", True), ("हाँ", True), ("ho aahe", True), ("true", True),
    ("nahi", False), ("nahi hai", False), ("na", False), ("नहीं", False), ("nako", False), ("false", False),
    ("pata nahi", False), ("hmm", None),
])
def test_parse_bool(text, expected):
    assert N.parse_bool(text) == expected


def make_state(events=None):
    return CallState(call_id="T1", booking={"id": "BK-1"}, today=TODAY,
                     on_event=(events.append if events is not None else lambda e: None))


@pytest.mark.parametrize("text,err", [("aaj", "same_day"), ("ravivar", "sunday"),
                                      ("2026-11-01", "past_date"), ("2027-02-01", "too_far")])
def test_date_validation(text, err):
    r = make_state().save("visit_date", text)
    assert not r["ok"] and r["error"] == err


def fill(state):
    for k, v in [("visit_date", "kal"), ("visit_time_slot", "subah"),
                 ("ladder_available", "haan"), ("water_source_available", "nahi")]:
        a = dispatch(state, "save_slot", {"key": k, "value": v})
    return a


def test_cannot_end_confirmed_without_all_slots():
    s = make_state()
    dispatch(s, "save_slot", {"key": "visit_date", "value": "kal"})
    assert not s.try_end("confirmed")["ok"]
    assert dispatch(s, "end_call", {"outcome": "confirmed"}).result["ok"] is False
    r = dispatch(s, "confirm_details", {"confirmed": True}).result
    assert r["error"] == "missing_slots" and "next_question" in r
    assert s.outcome is None


def test_full_happy_path_reads_back_then_confirms():
    events = []
    s = make_state(events)
    last = fill(s)
    assert last.speak and "Kya yeh sab sahi hai" in last.speak[0] and last.run_llm is False
    assert s.read_back and s.outcome is None
    end = dispatch(s, "confirm_details", {"confirmed": True})
    assert end.end and s.outcome == "confirmed"
    rec = s.record()
    assert rec["slots"] == {"visit_date": "2026-11-27", "visit_time_slot": "09-12",
                            "ladder_available": True, "water_source_available": False}
    types = [e["type"] for e in events]
    assert types.count("slot") == 4 and "readback" in types and "confirm" in types


def test_change_after_readback_forces_reread():
    s = make_state()
    fill(s)
    dispatch(s, "confirm_details", {"confirmed": False})
    assert not s.read_back
    assert dispatch(s, "confirm_details", {"confirmed": True}).result["error"] == "not_read_back"
    a = dispatch(s, "save_slot", {"key": "visit_time_slot", "value": "shaam"})
    assert a.speak and "shaam 3 se 6" in a.speak[0]  # correction is re-read automatically
    assert s.read_back and not s.confirmed
    assert dispatch(s, "confirm_details", {"confirmed": True}).end
    assert s.record()["slots"]["visit_time_slot"] == "15-18"


def test_save_after_readback_invalidates_confirmation():
    s = make_state()
    fill(s)
    s.confirmed = True  # simulate a confirm, then a late change
    a = dispatch(s, "save_slot", {"key": "ladder_available", "value": "nahi"})
    assert not s.confirmed and a.speak  # re-read, must confirm again
    assert s.try_end("confirmed")["error"] == "customer_has_not_confirmed"


def test_early_exits():
    s = make_state()
    assert dispatch(s, "end_call", {"outcome": "do_not_call"}).end and s.outcome == "do_not_call"
    s = make_state()
    assert not dispatch(s, "end_call", {"outcome": "callback"}).end
    assert dispatch(s, "end_call", {"outcome": "callback", "callback_time": "kal 5 baje"}).end
    s = make_state()
    assert not dispatch(s, "end_call", {"outcome": "no_answer"}).end  # system-only
    assert not dispatch(s, "end_call", {"outcome": "handed_to_human"}).end


def test_three_failures_escalate_to_human():
    s = make_state()
    for _ in range(2):
        assert not dispatch(s, "save_slot", {"key": "visit_date", "value": "pata nahi"}).end
    a = dispatch(s, "save_slot", {"key": "visit_date", "value": "pata nahi"})
    assert a.end and s.outcome == "handed_to_human"


def test_silence_three_times_is_no_answer():
    s = make_state()
    assert "Visit kis din" in on_silence(s).speak[0]
    assert not on_silence(s).end
    assert on_silence(s).end and s.outcome == "no_answer"


def test_nothing_after_end():
    s = make_state()
    dispatch(s, "transfer_to_human", {"reason": "angry"})
    assert dispatch(s, "save_slot", {"key": "visit_date", "value": "kal"}).result["error"] == "call_already_ended"
