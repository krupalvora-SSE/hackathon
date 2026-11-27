"""Regressions from live test calls on 2026-11-27."""
from datetime import date

import pytest

from agent.normalize import parse_date
from agent.stt_whisper import _fix, _unloop

TODAY = date(2026, 11, 27)  # Friday


@pytest.mark.parametrize("said,want", [
    ("मुझे 30 तारिक चाहिए", "2026-11-30"), ("तीस तारीख", "2026-11-30"), ("30 को कर दो", "2026-11-30"),
    ("मुझे 30 चाहिए", "2026-11-30"), ("२८ को", "2026-11-28"), ("5 tareekh ko", "2026-12-05"),
    ("पच्चीस तारीख़", "2026-12-25"), ("30 तारीख", "2026-11-30"),
])
def test_day_of_month_forms(said, want):
    assert parse_date(said, TODAY).isoformat() == want


@pytest.mark.parametrize("said", ["do ko", "3 baje chahiye", "date", "तारीख बताइए"])
def test_not_a_date(said):
    assert parse_date(said, TODAY) is None


def test_filler_loop_is_dropped_not_a_yes():
    assert _unloop("जी, जी, जी, जी, जी, जी, जी, जी, जी, जी, जी, ज", 4.55) == ""


def test_real_word_loop_kept_once():
    assert _unloop("नहीं नहीं नहीं नहीं नहीं", 3.0) == "नहीं"
    assert _unloop("जी, जी, जी", 1.0) == "जी, जी, जी"       # below the loop ratio: untouched


def test_ek_bhi_nahi_mishearing():
    assert _fix({"text": "एक बिने."})["text"] == "एक भी नहीं"


# CALL-fc2f980f: RECAP asked three times; the first re-ask switched to English
class _STT:
    def __init__(self, text, unsure=True):
        self.turn_unsure, self.turn_heard = unsure, [{"text": text, "unsure": unsure}]


class _Engine:
    def __init__(self, state="RECAP", lang="hi"):
        self.call = type("C", (), {"state": state, "lang": lang, "policies": {}})()


@pytest.mark.parametrize("heard,ask", [
    ("हाँ, confirm पुटू.", False),          # yes + confirm: act on it
    ("हाँ, confirm मत करो", True),           # a "no" inside
    ("confirm पुटू", True),                  # no opening yes
    ("Yes, I'm from India.", True),          # yes, but no second signal
])
def test_unsure_yes_with_confirm(heard, ask):
    from agent.bot import _ask_again
    assert bool(_ask_again({"intent": "affirm"}, _STT(heard), _Engine())) is ask


def test_recap_english_line_says_between():
    from agent.flow import FlowSpec
    line = FlowSpec.load().prompts["en"]["RECAP"]["repeat_with_time"]
    assert "between {band_spoken}" in line
