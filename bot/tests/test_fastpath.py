"""Fast path (agent/fastpath.py): plain turns are read by code and skip the LLM; anything else still goes to it.

Turns are the real Whisper transcripts of CALL-6517ea26, where each LLM turn added ~1.5 s of silence.
"""
import pytest

from agent.fastpath import fast_intent
from tests.test_flow import BOOKING, live


def at(*answers):
    """A live call driven through `answers` (intents) from the greeting."""
    call, _, _ = live(BOOKING)
    for on in answers:
        r = call.handle(on)
        assert r.error is None, (on, call.state, r.error)
    return call


def fast(call, heard, unsure=False):
    r = fast_intent(call, heard, unsure)
    return r and {k: v for k, v in r.items() if k != "language"}


def test_call_6517ea26_turns_skip_the_llm():
    call = at()
    assert call.state == "GREET"
    assert fast(call, "हाँ, मैं Amit बोल रहा हूँ.") is None            # another name than the one asked for
    assert fast(call, "हाँ, बोल रहा हूँ") == {"intent": "affirm"}
    call = at("affirm")
    assert call.state == "OFFER_PRIMARY"
    assert fast(call, "हाँ जी, मैं available हूँ.") == {"intent": "affirm"}
    call = at("affirm", "affirm")
    assert call.state == "ASK_SLOT"
    assert fast(call, "सुबह, 9 से 12") == {"intent": "gives_band", "band": "सुबह, 9 से 12"}
    call.handle("gives_band", {"band": "सुबह 9 से 12"})
    assert call.state == "READINESS_CHECK"
    assert fast(call, "E-type service ladder हाँ है ना।") is None     # "E-type service": not what was asked
    assert fast(call, "हाँ, A-type ladder है ना।") == {"intent": "affirm"}
    call.handle("affirm")
    assert fast(call, "पानी का source, हाँ, मिल जाएगा।") == {"intent": "affirm"}
    call.handle("affirm")
    assert fast(call, "लाइट का connection तो नहीं है।") == {"intent": "deny"}
    call.handle("deny")
    # now "by when can it be arranged?": not a yes/no question, the LLM reads the date
    assert fast(call, "Light का connection तो नहीं arrange हो पाएगा।") is None


@pytest.mark.parametrize("heard", [
    "नहीं, शाम को आना",              # a "no" that asks for another time
    "हाँ, लेकिन शाम को",
    "हाँ, शायद",                      # hedged
    "नहीं, मैं उनका भाई बोल रहा हूँ",  # household member
    "हाँ नहीं",
    "हम्म",
    "",
])
def test_anything_more_than_yes_or_no_goes_to_the_llm(heard):
    assert fast(at("affirm"), heard) is None


def test_never_on_an_unsure_transcript():
    assert fast(at("affirm"), "हाँ जी", unsure=True) is None


def test_choice_questions_are_not_yes_no():
    call = at("affirm", "deny")                                    # ALTERNATE_DAYS: two days offered
    assert call.state == "ALTERNATE_DAYS"
    assert fast(call, "हाँ") is None
    assert fast(at("affirm", "affirm"), "हाँ") is None            # ASK_SLOT: "which time?"


def test_band_must_be_one_free_offered_window():
    call = at("affirm", "affirm")
    assert fast(call, "शाम 3 से 6") is None                       # evening is not free on the primary day
    assert fast(call, "दोपहर 12 से 3") == {"intent": "gives_band", "band": "दोपहर 12 से 3"}
    assert fast(call, "सुबह") is None                             # no range: the LLM decides
    assert fast(call, "9 से 12 नहीं, 12 से 3") is None


def test_recap_needs_a_clear_yes():
    call = at("affirm", "affirm")
    call.handle("gives_band", {"band": "morning"})
    for _ in range(3):
        call.handle("affirm")
    assert call.state == "RECAP"
    assert fast(call, "जी") is None                                # weak yes: not enough to book
    assert fast(call, "हाँ जी") == {"intent": "affirm"}
    assert fast(call, "नहीं") == {"intent": "deny"}
