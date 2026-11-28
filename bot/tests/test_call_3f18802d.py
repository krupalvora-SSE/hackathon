"""Test call CALL-3f18802d: three faults heard on the call.

1. "माफ़ कीजिए, ठीक से सुनाई नहीं दिया। बढ़िया, आपके पास A-type ladder है?": the krupal Piper voice adds
   "बढ़िया" after "…नहीं दिया।" (its training clip 0032 held the next line's first word). The apology is
   reworded, and no praise word may follow an apology or open a re-ask.
2. "क्या आप, | फिर से दोहराएंगे?" was cut in two by VAD; alone the second half came back as "शलिसे, नहाँ है।".
   The turn is decoded whole, a garbled repeat phrase is rescored, and repeat requests skip the LLM.
3. The opener "हॅलो?" came out as "काई दो": the voice never trained on hello. Opener is a trained phrase now.

Tests that need Whisper / Piper and the call's recordings skip when those aren't on this machine.
"""
import asyncio
import sys
import wave
from pathlib import Path

import pytest

from agent import stt_whisper as W
from agent.engine import FlowEngine
from agent.fastpath import fast_intent
from agent.tts_normalizer import NORMALIZER
from tests.test_flow import BOOKING, SPEC, live

REC = W.RECORDINGS / "CALL-3f18802d"
needs_clips = pytest.mark.skipif(not (REC / "006.wav").exists() or not W.use_mlx(),
                                 reason="needs the call's recordings and MLX Whisper")


def at_ladder():
    call, _, _ = live()
    for on in ("affirm", "affirm", "affirm"):        # identity, primary day, any time
        call.handle(on)
    assert call.state == "READINESS_CHECK" and call.last_ref[1] == "ask_ladder"
    return call


# ---------------------------------------------------------------- 1. no "बढ़िया" after a sorry
def test_hindi_apology_avoids_the_phrase_the_voice_adds_badhiya_to():
    hi = SPEC.prompts["hi"]["_global"]
    assert "नहीं दिया" not in hi["didnt_catch"]
    assert hi["didnt_catch"] == "माफ़ कीजिए, मैं समझ नहीं पाया।"


def test_unclear_at_ladder_reasks_short_without_praise():
    call = at_ladder()
    r = call.handle("unclear")
    assert r.speak == ["माफ़ कीजिए, मैं समझ नहीं पाया। आपके पास A-type ladder है?"]
    assert not any("बढ़िया" in s for s in r.speak)


def test_repeat_request_at_ladder_is_the_short_question_only():
    call = at_ladder()
    r = call.handle("repeat_request")
    assert r.speak == ["आपके पास A-type ladder है?"] and call.unclear_streak == 0


def test_praise_is_dropped_after_any_apology_line_and_on_re_asks():
    call = at_ladder()
    sorry = call._global_line("didnt_catch")
    lines = [sorry, "बढ़िया। Service के लिए कुछ चीज़ें ready रखनी होंगी।", "आपके पास ladder है?"]
    assert call._no_praise_after_sorry(lines) == [sorry, "Service के लिए कुछ चीज़ें ready रखनी होंगी।",
                                                  "आपके पास ladder है?"]
    assert call._no_praise_after_sorry([f"{sorry} बढ़िया। आपके पास ladder है?"]) == [f"{sorry} आपके पास ladder है?"]
    assert call._no_praise_after_sorry(["बढ़िया। आगे"], re_ask=True) == ["आगे"]
    # a normal step keeps its praise
    assert call._no_praise_after_sorry(["बढ़िया। Service के लिए"]) == ["बढ़िया। Service के लिए"]


def test_praise_stays_on_a_normal_entry_into_the_checklist():
    call, _, _ = live()
    call.handle("affirm")
    call.handle("affirm")
    r = call.handle("affirm")
    assert r.speak[0].startswith("बढ़िया।")


# ---------------------------------------------------------------- 2. repeat requests
@pytest.mark.parametrize("heard", ["क्या आप फिर से दोहराएंगे?", "क्या आप फिर से दोराएंगे।", "सुनाई नहीं दिया",
                                   "फिर से बोलिए", "क्या बोला?", "दोबारा बोलिए", "sorry, repeat please",
                                   "समझ नहीं आया", "जी, फिर से बोलिए"])
def test_repeat_requests_skip_the_llm_even_when_unsure(heard):
    call = at_ladder()
    for unsure in (False, True):
        assert fast_intent(call, heard, unsure) == {"intent": "repeat_request", "language": "hi"}


@pytest.mark.parametrize("heard", ["नहीं है", "फिर से call करना", "हाँ है", "क्या आप", "शलिसे, नहाँ है।",
                                   "कल फिर से आना", "नहीं, समझ गया"])
def test_answers_are_not_read_as_repeat_requests(heard):
    r = fast_intent(at_ladder(), heard, True)
    assert not r or r["intent"] != "repeat_request"


def test_continuation_sends_only_the_new_words_of_a_turn_decoded_whole():
    joined = {"text": "क्या आप फिर से दोराएंगे।", "lang": "hi", "avg_logprob": -0.1, "compression_ratio": 1.0,
              "no_speech_prob": 0.0}
    r = W.continuation("क्या आप,", joined)
    assert r["text"] == "फिर से दोराएंगे।" and r["turn_text"] == joined["text"] and r["joined"]
    assert W.continuation("हाँ जी", joined) is None                 # decoded differently: decode the piece alone
    assert W.continuation("क्या आप फिर से दोराएंगे", joined) is None  # nothing new


def _pcm(name):
    with wave.open(str(REC / name)) as w:
        return w.readframes(w.getnframes())


@needs_clips
def test_split_repeat_request_is_heard_whole():
    """005 "क्या आप," then 006 (alone: "शलिसे, नहाँ है।") in one turn."""
    async def run():
        stt = W.WhisperHiEnSTTService()
        stt.start_turn()
        out = []
        for f in ("005.wav", "006.wav"):
            out += [fr.text async for fr in stt.run_stt(_pcm(f)) if hasattr(fr, "text")]
        return stt, out

    stt, out = asyncio.run(run())
    assert out[0].startswith("क्या आप") and out[1].startswith("फिर से")
    assert not stt.turn_unsure and stt.turn_heard[0]["text"].startswith("क्या आप फिर से")
    assert fast_intent(at_ladder(), stt.turn_heard[0]["text"], stt.turn_unsure)["intent"] == "repeat_request"


@needs_clips
def test_garbled_second_half_alone_is_rescored_as_a_repeat_request():
    import numpy as np
    x = np.frombuffer(_pcm("006.wav"), dtype=np.int16).astype(np.float32) / 32768
    r = W.transcribe_detailed(x)
    assert r["text"] in W.SHORT_REPEAT and r.get("fixed")


@needs_clips
@pytest.mark.parametrize("name,text", [("007.wav", "हाँ है"), ("009.wav", "हाँ जी")])
def test_clear_answers_on_the_same_call_are_untouched(name, text):
    import numpy as np
    x = np.frombuffer(_pcm(name), dtype=np.int16).astype(np.float32) / 32768
    assert W.transcribe_detailed(x)["text"].startswith(text)


# ---------------------------------------------------------------- 3. opener
def test_hindi_opener_is_a_trained_phrase_and_greet_does_not_repeat_namaste():
    eng = FlowEngine("O", BOOKING)
    assert eng.opener() == "नमस्ते जी?"
    assert not eng.opening()[0].startswith("नमस्ते")


def test_english_flow_keeps_hello():
    eng = FlowEngine("O", BOOKING)
    eng.call.lang = "en"
    assert eng.opener() == "Hello?"


def test_question_mark_only_follows_a_question_in_the_last_sentence():
    assert NORMALIZER.normalize("क्या राहुल जी बोल रहे हैं? मैं सौलर स्क्वैर से कृपाल।").endswith("कृपाल।")
    assert NORMALIZER.normalize("क्या आपके पास ladder है।").endswith("है?")
    assert NORMALIZER.normalize("ठीक है। क्या आप available हैं").endswith("हैं?")


# ---------------------------------------------------------------- voice dataset guard
def test_prepare_rejects_a_clip_holding_a_neighbours_word():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "voice_training"))
    from prepare import OVERRUN, norm, overrun

    line = norm("माफ़ कीजिए, ठीक से सुनाई नहीं दिया।")
    assert overrun(norm("माफ़ कीजिए") + norm("ठीक से सुनाई नहीं दिया, बढ़िया।"), line) >= OVERRUN   # 0032
    nxt = norm("बढ़िया। Service के लिए कुछ चीज़ें ready रखनी होंगी, बस confirm कर लेता हूँ।")
    assert overrun(norm("सर्विस के लिए कुछ चीज़े रेडी रखनी होगी बस कन्फर्म कर लेता हूँ"), nxt) >= OVERRUN  # 0033
    assert overrun(norm("माफ कीजिए, ठीक से सुनाई नहीं दिया"), line) < OVERRUN                         # clean take
