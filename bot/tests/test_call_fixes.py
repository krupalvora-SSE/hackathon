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


# CALL-418d20ee: "सोमवार, 30 नवंबर" heard as "सुमार थीस लंड"
OFFERED = [date(2026, 11, 30), date(2026, 12, 2)]  # Monday, Wednesday


@pytest.mark.parametrize("heard,want", [
    ("सुमार थीस लंड", "2026-11-30"), ("सोमार", "2026-11-30"), ("बुधवर", "2026-12-02"),
    ("30", "2026-11-30"), ("2 वाला", "2026-12-02"), ("monday", "2026-11-30"),
    ("बुआ तो गजो जो", None), ("समय नहीं है", None), ("शनिवार", None),
    ("सोमवार या बुधवार", None),                 # two days: no guess
    ("सोमवार कर दो", "2026-11-30"),             # "दो" is not the 2nd
])
def test_match_offered_day(heard, want):
    from agent.normalize import match_offered_day
    d = match_offered_day(heard, OFFERED)
    assert (d.isoformat() if d else None) == want


class _Offering:
    def __init__(self):
        self.call = type("C", (), {"state": "ALTERNATE_DAYS", "lang": "hi",
                                   "offered": [{"date": "2026-11-30"}, {"date": "2026-12-02"}]})()


def test_garbled_day_choice_becomes_that_day():
    from agent.bot import _offered_day_heard
    assert _offered_day_heard({"intent": "unclear"}, _STT("सुमार थीस लंड"), _Offering()) == "2026-11-30"
    assert _offered_day_heard({"intent": "unclear"}, _STT("नहीं, सुमार नहीं"), _Offering()) is None
    assert _offered_day_heard({"intent": "deny"}, _STT("सुमार थीस"), _Offering()) is None


# CALL-5b614da9: water question asked three times, then handed to a senior
@pytest.mark.parametrize("heard,ask", [
    ("येस, है.", False), ("हाँ मिजया लाँ", False),
    ("हाँ, शायद", True), ("हाँ नहीं है", True), ("हाम में जाइबा", True),
])
def test_unsure_plain_yes_at_readiness(heard, ask):
    from agent.bot import _ask_again
    engine = _Engine(state="READINESS_CHECK")
    assert bool(_ask_again({"intent": "affirm", "readiness": {"water": True}}, _STT(heard), engine)) is ask


def test_unsure_yes_still_asked_again_at_recap():
    from agent.bot import _ask_again
    assert _ask_again({"intent": "affirm"}, _STT("हाँ मिजया लाँ"), _Engine(state="RECAP"))


def test_same_reading_twice_is_taken_outside_booking_steps():
    from agent.bot import _ask_again
    args, engine = {"intent": "affirm", "readiness": {"water": True}}, _Engine(state="READINESS_CHECK")
    assert _ask_again(args, _STT("हाम में जाइबा"), engine)            # first time: ask again
    assert not _ask_again(args, _STT("हाम में जाइबा"), engine)        # same reading again: take it
    recap = _Engine(state="RECAP")
    assert _ask_again({"intent": "affirm"}, _STT("हाम में"), recap)
    assert _ask_again({"intent": "affirm"}, _STT("हाम में"), recap)   # never at a booking step


def test_a_clear_turn_forgets_the_last_unsure_reading():
    from agent.bot import _ask_again
    args, engine = {"intent": "affirm", "readiness": {"water": True}}, _Engine(state="READINESS_CHECK")
    _ask_again(args, _STT("हाम में जाइबा"), engine)
    _ask_again(args, _STT("हाँ जी", unsure=False), engine)
    assert _ask_again(args, _STT("हाम में जाइबा"), engine)


# CALL-2c3e81bb: "बारह से तीन" heard as "बार से ती" / "बाराई से ती"
@pytest.mark.parametrize("heard", ["बार से ती", "बाराई से ती?"])
def test_garbled_twelve_to_three(heard):
    from agent.flow import FlowSpec, band_from_range
    assert band_from_range(heard, FlowSpec.load().policies) == "afternoon"


# CALL-24b72b22 / CALL-392c194e: "हाँ, बोल रहा हूँ" garbled at the identity question was asked again
@pytest.mark.parametrize("heard,state,intent,take", [
    ("हाँ, सोल पालोग?", "GREET", "unclear", True),
    ("हाँ बुल रिम।", "GREET", "unclear", True),
    ("हाँ नहीं", "GREET", "unclear", False),           # a "no" inside
    ("हाँ शायद", "GREET", "unclear", False),           # a hedge
    ("सोल पालोग", "GREET", "unclear", False),          # no opening yes
    ("हाँ, सोल पालोग?", "OFFER_PRIMARY", "unclear", False),  # only the identity question
    ("हाँ, सोल पालोग?", "GREET", "deny", False),       # only overrides "unclear"
])
def test_identity_yes_by_opening_word(heard, state, intent, take):
    from agent.bot import _identity_yes_heard
    assert _identity_yes_heard({"intent": intent}, _STT(heard), _Engine(state)) is take


def test_greeting_clears_the_misheard_first_reply():
    # CALL-1e03b45f: "Hello" heard as "प्रोग" (unsure) went to the greeting, not the LLM, so it was never consumed;
    # the clear "हाँ" to "क्या Vikas जी बोल रहे हैं?" then counted as unsure and was asked again
    import asyncio
    from agent.bot import GreetingGate
    from agent.stt_whisper import WhisperHiEnSTTService

    stt = WhisperHiEnSTTService.__new__(WhisperHiEnSTTService)
    stt.turn_unsure, stt.turn_heard, stt.turn_consumed = True, [{"text": "प्रोग", "unsure": True}], False
    greeted = []

    async def speak(): greeted.append(True)

    asyncio.run(GreetingGate(speak, stt).greet())
    stt.start_turn()                                   # the customer answers the greeting
    assert greeted and not stt.turn_unsure and stt.turn_heard == []


def test_run_bot_never_uses_a_local_function_before_it_is_defined():
    """CALL-e4fdafd8 failed at start: FastPath(engine, stt, act) ran before `async def act` further down
    (UnboundLocalError). No test builds the live pipeline, so check _run_bot's body statically."""
    import ast
    import inspect
    import textwrap

    from agent import bot

    fn = ast.parse(textwrap.dedent(inspect.getsource(bot._run_bot))).body[0]
    defs = {s.name: s.lineno for s in fn.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def eager_names(node):  # names evaluated right away: skip nested function bodies and lambdas
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in getattr(node, "decorator_list", []):
                yield from eager_names(d)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            yield node
        for child in ast.iter_child_nodes(node):
            yield from eager_names(child)

    early = [(n.id, n.lineno) for stmt in fn.body for n in eager_names(stmt)
             if n.id in defs and n.lineno < defs[n.id]]
    assert not early, early


def test_keep_warm_skips_while_a_real_decode_runs(monkeypatch):
    """CALL-786626d2: weights paged out while idle, first reply took 32 s. The keep-warm decode must never
    make a customer's decode wait: it skips when the MLX lock is held."""
    from agent import stt_whisper as sw

    calls = []
    monkeypatch.setattr(sw, "use_mlx", lambda: True)
    monkeypatch.setattr(sw, "_warmed", True)
    monkeypatch.setattr(sw, "_transcribe_mlx", lambda a, p=None: calls.append(len(a)) or {})
    with sw._mlx_lock:
        assert sw.refresh() is None
    assert calls == []
    assert sw.refresh() is not None and calls == [16000]
    assert not sw._mlx_lock.locked()


@pytest.mark.skipif(not __import__("agent.stt_whisper", fromlist=["use_mlx"]).use_mlx(), reason="needs MLX Whisper")
def test_mlx_cache_does_not_grow_across_decodes():
    """CALL-26e8229e: MLX kept every freed buffer; the bot reached 11 GB after one call and macOS swapped the
    model out, so the first reply took 15 s. After any number of decodes the cache must stay empty."""
    import numpy as np

    from agent import stt_whisper as sw

    rng = np.random.default_rng(1)
    for secs in (1.0, 2.5, 4.0):
        sw.transcribe_detailed((rng.standard_normal(int(16000 * secs)) * 0.01).astype(np.float32))
    assert sw.mlx_memory()["cache"] <= sw.MLX_CACHE_MB
