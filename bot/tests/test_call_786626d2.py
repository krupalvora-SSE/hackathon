"""Test call CALL-786626d2: the customer had no solar plant, and the bot kept scheduling.

1. "नहीं, मेरे घर पे कोई सुदार स्टांड नहीं है" was read as deny (-> "जैसे 5, या अगले सोमवार"), "कोई plant है ही
   नहीं" as not_serviceable (-> "site कब ready होगी?"), "SolarSquare है यह नहीं" as wrong_person (-> "क्या आप
   विकास जी के घर से…?"). New global intent no_solar_plant -> NO_PLANT_CLOSE; code reads it (and "wrong number")
   itself, so neither the LLM nor the step can keep the call going.
2. "जी, एक मिनट।" / "अच्छा…" / "समझ गया।" played before those replies, and "माफ़ कीजिए, मैं समझ नहीं पाया…" was
   followed 6 s later by "हैलो, क्या आप मुझे सुन पा रहे हैं? …" - two re-asks for one turn.
3. Whisper: "नमस्टे" (unsure) for the greeting and "विकार दिसे बाती" for "विकास जी से बात…"; Smart Turn waited
   3 s on "Hello?"; the first decode took 32 s (weights paged out, see test_call_fixes).

Tests that need Whisper and the call's recordings skip when those aren't on this machine.
"""
import asyncio
import wave
from types import SimpleNamespace

import pytest

from agent import normalize as N
from agent import stt_whisper as W
from agent.bot import _leaving_heard
from agent.config import FIELDS
from agent.fastpath import fast_intent
from agent.filler import FILLERS, Filler
from tests.test_flow import SPEC, live

REC = W.RECORDINGS / "CALL-786626d2"
needs_clips = pytest.mark.skipif(not (REC / "007.wav").exists() or not W.use_mlx(),
                                 reason="needs the call's recordings and MLX Whisper")

# What Whisper heard on the call, in order
NO_PLANT = ["नहीं, मेरे घर पे कोई सुदार स्टांड नहीं है।", "अरे मेरे घर पे कोई plant है ही नहीं।",
            "अरे भाई, मेरे घर पे SolarSquare है यह नहीं।", "अरे भाई मेरे घर पे solar system है ही नहीं",
            "हमने solar लगवाया ही नहीं", "मेरे घर पे कोई सोलर प्लांट नहीं है"]


def at(state_path):
    call, _, _ = live()
    for on in state_path:
        call.handle(on)
    return call


# ---------------------------------------------------------------- 1. not our customer: end the call
@pytest.mark.parametrize("text", NO_PLANT)
def test_no_plant_is_read_by_code(text):
    assert N.leaving_intent(text) == "no_solar_plant"


@pytest.mark.parametrize("text", ["नहीं, wrong number.", "galat number hai", "आपने गलत नंबर लगाया है",
                                  "ये number गलत है", "रॉंग नंबर"])
def test_wrong_number_is_read_by_code(text):
    assert N.leaving_intent(text) == "wrong_number"


@pytest.mark.parametrize("text", [
    "नहीं", "मेरे पास ladder नहीं है", "solar panel साफ़ नहीं है", "panel पे ladder नहीं है",
    "solar नहीं, शाम को आना", "हाँ जी solar system है", "number सही है", "मेरे घर पे कोई नहीं है",
    "पानी का source नहीं है", "solar panel पे जंग लग गया है", "उस दिन नहीं हो पाएगा"])
def test_ordinary_answers_are_not_read_as_leaving(text):
    assert N.leaving_intent(text) is None


@pytest.mark.parametrize("path,state", [
    ([], "GREET"), (["affirm"], "OFFER_PRIMARY"), (["affirm", "deny"], "ALTERNATE_DAYS"),
    (["wrong_person"], "WRONG_PERSON"), (["affirm", "not_serviceable"], "SITE_ON_HOLD"),
    (["affirm", "affirm"], "ASK_SLOT")])
def test_no_solar_plant_ends_the_call_from_any_step(path, state):
    call = at(path)
    assert call.state == state
    r = call.handle("no_solar_plant")
    assert r.end and call.outcome == "no_solar_plant" and call.state == "NO_PLANT_CLOSE"
    line = " ".join(r.speak)
    assert line.startswith("माफ़ कीजिए") and "कोई बात नहीं" not in line
    assert not any(w in line for w in ("तारीख", "date", "slot", "घर से"))     # no scheduling, no identity re-ask


def test_fast_path_ends_the_call_without_the_llm():
    call = at(["affirm"])                                     # the date offer, where "सुदार स्टांड" was heard
    assert fast_intent(call, NO_PLANT[0], unsure=False) == {"intent": "no_solar_plant", "language": "hi"}
    call = at(["affirm", "deny"])
    assert fast_intent(call, "नहीं, wrong number.", unsure=False)["intent"] == "wrong_number"
    assert fast_intent(call, NO_PLANT[0], unsure=True) is None    # never on a transcript Whisper doubts


@pytest.mark.parametrize("llm", ["deny", "not_serviceable", "wrong_person", "reports_issue"])
def test_code_overrides_what_the_llm_read(llm):
    call = at(["affirm"])
    stt = SimpleNamespace(turn_unsure=False, turn_heard=[{"text": NO_PLANT[1]}])
    assert _leaving_heard({"intent": llm}, stt, SimpleNamespace(call=call)) == "no_solar_plant"
    stt.turn_unsure = True
    assert _leaving_heard({"intent": llm}, stt, SimpleNamespace(call=call)) is None


def test_llm_prompt_names_the_intent():
    from agent.prompts import flow_system_prompt

    p = flow_system_prompt(at([]))
    assert "no_solar_plant (any time)" in p and "never deny, not_serviceable" in p


# ---------------------------------------------------------------- 2. fillers and double re-asks
def _fillers(text, unsure=False):
    said = []

    async def run():
        async def speak(line):
            said.append(line)
        f = Filler(speak, 0.01, skip_turns=0)
        f.customer_stopped(unsure=unsure, text=text)
        await asyncio.sleep(0.05)
    asyncio.run(run())
    return said


@pytest.mark.parametrize("text", [*NO_PLANT, "नहीं, wrong number.", "नहीं", "नहीं, उस दिन नहीं हो पाएगा"])
def test_no_filler_before_a_no_or_a_not_our_customer_turn(text):
    assert _fillers(text) == []


def test_filler_still_plays_on_an_ordinary_slow_turn():
    assert _fillers("शनिवार को शाम में आ जाना") == [FILLERS[0]]


def test_filler_never_claims_it_understood():
    assert "समझ गया।" not in FILLERS


def test_silence_right_after_a_re_ask_waits_quietly_once():
    call = at([])
    assert call.handle("unclear").speak[0].startswith("माफ़ कीजिए")
    quiet = call.silence()
    assert quiet.speak == [] and quiet.idle_timeout and not quiet.end and call.silences == 0
    assert "सुन पा रहे" in call.silence().speak[0]           # still nothing: now the "can you hear me?"


def test_silence_after_a_normal_question_still_reprompts():
    call = at(["affirm"])
    assert "सुन पा रहे" in call.silence().speak[0]


def test_the_quiet_wait_does_not_change_when_silence_ends_the_call():
    call = at(["affirm", "unclear"])
    steps = [call.silence() for _ in range(4)]
    assert [bool(s.speak) for s in steps[:3]] == [False, True, True] and steps[3].end
    assert call.outcome == "no_answer"


# ---------------------------------------------------------------- 3. STT and turn-taking
def test_misspelt_words_are_fixed_without_changing_confidence():
    r = W._fix({"text": "नमस्टे,", "lang": "hi", "avg_logprob": -0.4, "compression_ratio": 1.0, "no_speech_prob": 0})
    assert r["text"] == "नमस्ते," and r["heard_as"] == "नमस्टे," and not r.get("fixed")
    assert W._fix({"text": "नहीं, रॉंग नंबर", "lang": "hi", "avg_logprob": -0.1, "compression_ratio": 1,
                   "no_speech_prob": 0})["text"] == "नहीं, wrong नंबर"


def test_call_prompt_leads_with_greeting_and_devanagari_name():
    p = W.call_prompt("Vikas Sharma")
    assert p.startswith("नमस्ते जी, विकास जी।") and p.endswith(W.VOCAB_PROMPT)
    assert W.call_prompt("") == f"नमस्ते जी। {W.VOCAB_PROMPT}"
    assert "plant" not in p and "wrong" not in p           # those made Whisper drop the "नहीं"


def test_bot_says_vikas_not_vikas_with_short_a():
    from agent.devanagari import to_devanagari

    assert to_devanagari("Vikas") == "विकास"


def test_namaste_is_a_short_answer_candidate():
    assert "नमस्ते" in W.SHORT_OTHER and "नमस्ते" not in W.SHORT_YES


def test_smart_turn_wait_is_shorter_than_pipecats_3s():
    f = next(f for f in FIELDS if f["key"] == "turn_stop_secs")
    assert f["default"] < 3


def _pcm(name):
    import numpy as np

    with wave.open(str(REC / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


@needs_clips
def test_greeting_heard_as_namaste_and_sure():
    for f in ("001.wav", "002.wav"):
        r = W.transcribe_detailed(_pcm(f), prompt=W.call_prompt("Vikas"))
        assert r["text"].startswith("नमस्ते") and not W.is_unsure(r)


@needs_clips
def test_name_is_heard_with_the_name_in_the_prompt():
    r = W.transcribe_detailed(_pcm("006.wav"), prompt=W.call_prompt("Vikas"))
    assert "विकास" in r["text"]


@needs_clips
@pytest.mark.parametrize("f,want", [("007.wav", "no_solar_plant"), ("008.wav", "no_solar_plant"),
                                    ("011.wav", "wrong_number")])
def test_recorded_turns_end_the_call(f, want):
    r = W.transcribe_detailed(_pcm(f), prompt=W.call_prompt("Vikas"))
    assert not W.is_unsure(r) and N.leaving_intent(r["text"]) == want
