import asyncio
from datetime import date

import pytest

from agent.devanagari import devanagari_transform, to_devanagari
from agent.tts_normalizer import NORMALIZER, HindiHinglishTTSNormalizer, TTSNormalizerConfig

N = NORMALIZER.normalize


def test_numbers_stay_digits_for_espeak():
    # espeak-ng reads them right offline (52 -> "बावन"); a word mapper said "पचास दो"
    assert N("सोमवार 30 को सुबह 9 से 12 और शाम 3 से 6") == "सोमवार 30 को सुबह 9 से 12 और शाम 3 से 6"


def test_whitespace_collapsed():
    assert N("   कुल   15   पैनल   हैं   ") == "कुल 15 पैनल हैं"


def test_hero_respelled_with_devanagari_boundaries():
    assert N("सर्विस हिरो आएंगे") == "सर्विस हीरो आएंगे"
    assert N("सर्विस हिरोज़") == "सर्विस हिरोज़"     # inside a longer word: untouched


def test_thik_hai_is_left_alone():
    assert N("जी ठीक है") == "जी ठीक है"           # already [hɛː]; "हे" would be [heː]


def test_custom_replacements_match_words_ending_in_a_vowel_sign():
    n = HindiHinglishTTSNormalizer(TTSNormalizerConfig(replacements=[("ठीक है", "X")]))
    assert n.normalize("जी ठीक है।") == "जी X।"    # r"\bठीक है\b" never matched this


@pytest.mark.parametrize(("text", "out"), [
    ("सुबह 9 से 12 के", "सुबह 9 से 12"),
    ("शाम 3 से 6 के।", "शाम 3 से 6।"),
    ("शाम 3 से 6 के?", "शाम 3 से 6?"),
    ("छत के पास पानी है", "छत के पास पानी है"),
    ("आपके", "आपके"),
])
def test_dangling_ke(text, out):
    assert N(text) == out


@pytest.mark.parametrize(("text", "out"), [
    ("क्या आप उस दिन available रहेंगे।", "क्या आप उस दिन available रहेंगे?"),
    ("कब तक connection आ जाएगा.", "कब तक connection आ जाएगा?"),
    ("कितने बजे ठीक रहेगा", "कितने बजे ठीक रहेगा?"),
    ("क्या आप तैयार हैं?", "क्या आप तैयार हैं?"),
    ("धन्यवाद।", "धन्यवाद।"),
    ("कबड्डी खेलेंगे।", "कबड्डी खेलेंगे।"),       # "कब" only as a whole word
])
def test_questions_end_with_question_mark(text, out):
    assert N(text) == out


def test_wired_into_the_voice_path():
    assert to_devanagari("क्या service hero कल आएंगे।", today=date(2026, 11, 27)).endswith("?")
    assert asyncio.run(devanagari_transform("सर्विस हिरो आएंगे")) == "सर्विस हीरो आएंगे"


def test_training_transcripts_untouched():
    assert to_devanagari("क्या आप तैयार हैं।", near_dates=False) == "क्या आप तैयार हैं।"


def test_changes_are_logged_at_debug():
    from loguru import logger

    seen = []
    sink = logger.add(seen.append, level="DEBUG", format="{message}")
    try:
        N("सर्विस हिरो आएंगे")
        N("धन्यवाद।")                                # unchanged: no log line
    finally:
        logger.remove(sink)
    lines = [m for m in seen if "tts normalize" in m]
    assert len(lines) == 1 and "सर्विस हीरो" in lines[0]
