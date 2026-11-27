"""Spoken-word fixes on the Piper path (agent/devanagari.py): the Hindi voice reads only Devanagari."""
from agent.devanagari import to_devanagari


def test_a_type_is_spoken_as_e_type():
    # read as "air type" otherwise
    assert to_devanagari("क्या आपके पास A-type ladder है?") == "क्या आपके पास ए-टाइप लैडर है?"
    assert to_devanagari("a-type ladder").startswith("ए-टाइप")


def test_main_is_spoken_as_i_not_maintenance():
    assert to_devanagari("main bol raha hoon") == "मैं बोल रहा हूँ"
    assert to_devanagari("maintenance due hai") == "मेंटेनेंस ड्यू है"


def test_whisper_short_yes_mishearings_are_read_as_yes():
    from agent import normalize as N
    from agent.stt_whisper import _fix, is_unsure

    base = {"lang": "hi", "avg_logprob": -0.36, "compression_ratio": 0.5, "no_speech_prob": 0.0}
    r = _fix({**base, "text": "अजी."})
    assert r["text"] == "हाँ जी" and not is_unsure(r) and N.clear_yes(r["text"])
    assert _fix({**base, "text": "कन फो।"})["text"] == "confirm"
    assert _fix({**base, "text": "अजी सुनिए"})["text"] == "अजी सुनिए"  # only whole replies
