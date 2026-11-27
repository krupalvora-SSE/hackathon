"""Open-source voice (Piper): Devanagari rewriting, the voice engines, and a clear error if Piper fails."""
import asyncio
import re

import yaml

from agent import bot, config
from datetime import date

from agent.devanagari import devanagari_transform, to_devanagari

LATIN = re.compile(r"[A-Za-z]")


def test_flow_and_fixed_lines_end_up_fully_devanagari():
    from agent import prompts

    hi = yaml.safe_load(open(config.Path(__file__).resolve().parents[2] / "flows/service-scheduling/prompts/hi.yaml"))
    lines = [re.sub(r"\{\w+\}", "", v) for st in hi.values() for v in st.values()]
    lines += [*prompts.GOODBYES.values(), *prompts.SILENCE_PROMPTS, "Hello?"]
    for line in lines:
        out = to_devanagari(line)
        assert not LATIN.search(out), (line, out)


def test_known_words_names_and_mai():
    assert to_devanagari("आपके solar plant का slot") == "आपके सोलर प्लांट का स्लॉट"
    assert to_devanagari("SolarSquare, A-type ladder") == "सोलरस्क्वेयर, ए-टाइप लैडर"
    assert to_devanagari("Sharma ji") == "शर्मा जी"          # unknown name: final vowel lengthened
    assert to_devanagari("Dhrumil") == "ध्रुमिल"              # no trailing halant
    assert to_devanagari("mai baad mai call karunga") .startswith("मैं बाद में कॉल")
    out = to_devanagari("पहले से Devanagari है।")  # fallback can't know mid-word long vowels: देवनगरी
    assert out.startswith("पहले से ") and out.endswith(" है।") and not LATIN.search(out)
    assert asyncio.run(devanagari_transform("Hello?", "sentence")) == "हॅलो?"   # English "hɛlo", not "heːlo"


FAR = date(2026, 1, 1)  # months away: dates keep their month


def test_dates_are_spoken_as_written():
    assert to_devanagari("Shanivar, 29 November", FAR) == "शनिवार, 29 नवंबर"
    assert to_devanagari("Somvar, 1 December ko", FAR) == "सोमवार, 1 दिसंबर को"
    assert to_devanagari("2026-11-29 ko", FAR) == "29 नवंबर को"
    assert to_devanagari("29/11", FAR) == "29 नवंबर"
    assert to_devanagari("Saturday 29th Nov", FAR) == "सैटरडे 29 नवंबर"


def test_near_dates_drop_the_month():
    today = date(2026, 11, 27)
    assert to_devanagari("सोमवार, 30 नवंबर को आप अवेलेबल हैं?", today) == "सोमवार, 30 को आप अवेलेबल हैं?"
    assert to_devanagari("Shanivar, 28 November", today) == "शनिवार, 28"
    assert to_devanagari("30 नवंबर या 7 दिसंबर", today) == "30 या 7"
    assert to_devanagari("2026-12-11 ko", today) == "11 को"          # day 14: still near
    assert to_devanagari("12 दिसंबर", today) == "12 दिसंबर"          # day 15: keep the month
    assert to_devanagari("26 नवंबर", today) == "26 नवंबर"            # past date: keep it
    assert to_devanagari("2 जनवरी", date(2026, 12, 25)) == "2"       # across the new year


def test_piper_voices_are_male_and_only_local_engines():
    f = config.BY_KEY["piper_voice"]
    assert [o[0] for o in f["options"]] == ["hi_IN-pratham-medium", "hi_IN-rohan-medium", "hi_IN-jasir-medium",
                                         "hi_IN-krupal-medium"]
    tts = config.BY_KEY["tts_provider"]
    assert [o[0] for o in tts["options"]] == ["piper", "piper_clone"] and tts["default"] == "piper"


def test_piper_failure_is_a_clear_error(monkeypatch):
    import pytest

    from agent import voice_local

    def boom(*a, **k):
        raise RuntimeError("voice download failed")
    monkeypatch.setattr(voice_local, "BilingualPiperTTSService", boom)
    cfg = {"tts_provider": "piper", "piper_voice": "hi_IN-pratham-medium", "tts_pace": None,
           "piper_noise_scale": 0.33, "piper_noise_w": 0.5}
    with pytest.raises(RuntimeError, match="hi_IN-pratham-medium unavailable: voice download failed"):
        bot._piper_tts(cfg, "T1", 8000)


def test_english_sentences_skip_devanagari_and_hindi_lines_have_no_english():
    from agent.devanagari import is_english

    assert is_english("And is there a light connection there?")
    assert not is_english("Hello?") and not is_english("Rahul ji")              # opener, lone name
    assert not is_english("Theek hai ji, kal call karenge")                     # Roman Hinglish
    assert not is_english("नमस्ते Rahul जी, आपका समय पक्का हो गया है।")
    assert asyncio.run(devanagari_transform("Shall I confirm it?")) == "Shall I confirm it?"
    hi = yaml.safe_load(open(config.Path(__file__).resolve().parents[2] / "flows/service-scheduling/prompts/hi.yaml"))
    for st in hi.values():  # lines are Hinglish; Piper must still get pure Devanagari after rewriting
        for v in st.values():
            spoken = to_devanagari(re.sub(r"\{\w+\}", "", v))
            assert not LATIN.search(spoken), (v, spoken)


def test_bilingual_piper_picks_voice_by_language():
    from pipecat.services.piper.tts import PiperTTSService

    from agent.voice_local import BilingualPiperTTSService

    tts = BilingualPiperTTSService(en_voice="en_US-ryan-medium",
                                   settings=PiperTTSService.Settings(voice="hi_IN-pratham-medium"))
    assert tts._pick("Shall I confirm it for Saturday?")[0] == "en_US-ryan-medium"
    assert tts._pick("क्या मैं यह पक्का कर दूँ?")[0] == "hi_IN-pratham-medium"
    assert tts._pick("Hello?")[0] == "hi_IN-pratham-medium"


def test_hinglish_words_get_real_spellings_not_letter_by_letter():
    # the phonetic fallback read these as तेच्निचिअन / सोर्र्य / बोओकिङ्ग / चोम्प्लैन्त on live calls
    assert to_devanagari("Sorry, technician कल आएगा, booking confirm है।") == "सॉरी, टेक्नीशियन कल आएगा, बुकिंग कन्फ़र्म है।"
    assert to_devanagari("complaint price") == "कंप्लेंट प्राइस"
    script = open(config.Path(__file__).resolve().parents[1] / "voice_training/script_hinglish.txt").read()
    from agent.devanagari import WORDS
    unknown = {w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", script) if w.lower() not in WORDS}
    assert not unknown, f"training script words without a real spelling: {unknown}"
