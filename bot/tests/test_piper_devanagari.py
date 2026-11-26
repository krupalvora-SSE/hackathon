"""Open-source voice (Piper): Devanagari rewriting, engine switch and fallback to Sarvam."""
import asyncio
import re

import yaml

from agent import bot, config
from agent.devanagari import devanagari_transform, to_devanagari
from agent.voice import clone_in_use

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
    assert asyncio.run(devanagari_transform("Hello?", "sentence")) == "हेलो?"


def test_piper_voices_are_male_and_piper_disables_clone(monkeypatch):
    f = config.BY_KEY["piper_voice"]
    assert [o[0] for o in f["options"]] == ["hi_IN-pratham-medium", "hi_IN-rohan-medium", "hi_IN-jasir-medium"]
    monkeypatch.setattr("agent.voice.active_voice_id", lambda: "svc-x")
    assert clone_in_use({"tts_provider": "sarvam", "tts_source": "clone"}) == "svc-x"
    assert clone_in_use({"tts_provider": "piper", "tts_source": "clone"}) is None


def test_piper_failure_falls_back_to_sarvam(monkeypatch):
    import pipecat.services.piper.tts as piper_tts

    def boom(*a, **k):
        raise RuntimeError("voice download failed")
    monkeypatch.setattr(piper_tts, "PiperTTSService", boom)
    cfg = {"tts_provider": "piper", "piper_voice": "hi_IN-pratham-medium", "tts_pace": None}
    assert bot._piper_tts(cfg, "T1", 8000) is None and cfg["tts_provider"] == "sarvam"


def test_english_sentences_skip_devanagari_and_hindi_lines_have_no_english():
    from agent.devanagari import is_english

    assert is_english("And is there a light connection there?")
    assert not is_english("Hello?") and not is_english("Rahul ji")              # opener, lone name
    assert not is_english("Theek hai ji, kal call karenge")                     # Roman Hinglish
    assert not is_english("नमस्ते Rahul जी, आपका समय पक्का हो गया है।")
    assert asyncio.run(devanagari_transform("Shall I confirm it?")) == "Shall I confirm it?"
    hi = yaml.safe_load(open(config.Path(__file__).resolve().parents[2] / "flows/service-scheduling/prompts/hi.yaml"))
    for st in hi.values():
        for v in st.values():
            assert not LATIN.search(re.sub(r"\{\w+\}", "", v)), v   # plain Hindi: Piper reads it natively


def test_bilingual_piper_picks_voice_by_language():
    from pipecat.services.piper.tts import PiperTTSService

    from agent.voice_local import BilingualPiperTTSService

    tts = BilingualPiperTTSService(en_voice="en_US-ryan-medium",
                                   settings=PiperTTSService.Settings(voice="hi_IN-pratham-medium"))
    assert tts._pick("Shall I confirm it for Saturday?")[0] == "en_US-ryan-medium"
    assert tts._pick("क्या मैं यह पक्का कर दूँ?")[0] == "hi_IN-pratham-medium"
    assert tts._pick("Hello?")[0] == "hi_IN-pratham-medium"
