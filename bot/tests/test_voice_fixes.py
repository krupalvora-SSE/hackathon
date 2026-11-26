"""Spoken-word fixes on the Piper path (agent/devanagari.py): the Hindi voice reads only Devanagari."""
from agent.devanagari import to_devanagari


def test_a_type_is_spoken_as_e_type():
    # read as "air type" otherwise
    assert to_devanagari("क्या आपके पास A-type ladder है?") == "क्या आपके पास ए-टाइप लैडर है?"
    assert to_devanagari("a-type ladder").startswith("ए-टाइप")


def test_main_is_spoken_as_i_not_maintenance():
    assert to_devanagari("main bol raha hoon") == "मैं बोल रहा हूँ"
    assert to_devanagari("maintenance due hai") == "मेंटेनेंस ड्यू है"
