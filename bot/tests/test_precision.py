"""Precision guards: ask again instead of acting on a misheard turn (agent/bot.py, agent/stt_whisper.py).

Cases are real transcripts from a 2026-11-26 test call where garbled audio was read as "yes" and booked.
"""
import asyncio
import json
from types import SimpleNamespace

from agent import normalize as N
from agent.bot import _ask_again


def test_clear_yes_needs_a_real_yes_word():
    for said in ("हाँ जी", "हाँ कर दो", "ji haan theek hai", "bilkul", "चलेगा", "ok confirm kar do", "हां"):
        assert N.clear_yes(said), said
    for said in ("अजी है नगा", "प्रूब", "है", "जी", "हजी", "नहीं", "haan nahi", "कर लो कर दो कर दो नहीं"):
        assert not N.clear_yes(said), said


def _stt(heard, unsure=False):
    return SimpleNamespace(turn_unsure=unsure, turn_heard=[{"text": t, "unsure": unsure} for t in heard])


def _engine(state):
    return SimpleNamespace(call=SimpleNamespace(state=state))


def test_unsure_transcript_is_asked_again_whatever_the_llm_read():
    assert _ask_again({"intent": "affirm"}, _stt(["झाल"], unsure=True), _engine("OFFER_PRIMARY"))
    assert _ask_again({"intent": "wrong_number"}, _stt(["कर लो कर दो कर दो"], unsure=True), _engine("GREET"))
    # already safe readings pass through: a Marathi speaker is legitimately low-confidence Hindi
    assert _ask_again({"intent": "other_language"}, _stt(["काय झालं"], unsure=True), _engine("GREET")) is None
    assert _ask_again({"intent": "unclear"}, _stt(["x"], unsure=True), _engine("GREET")) is None


def test_booking_needs_a_clear_yes_at_the_recap():
    assert _ask_again({"intent": "affirm"}, _stt(["प्रूब"]), _engine("RECAP"))            # booked on this before
    assert _ask_again({"intent": "affirm"}, _stt(["अजी है नगा"]), _engine("HOLD_CONSENT"))
    assert _ask_again({"intent": "affirm"}, _stt(["हाँ जी, कर दो"]), _engine("RECAP")) is None
    assert _ask_again({"intent": "deny"}, _stt(["नहीं"]), _engine("RECAP")) is None       # no is never risky
    # outside the commit steps a confident yes-ish answer is fine (the recap catches it later)
    assert _ask_again({"intent": "affirm"}, _stt(["है"]), _engine("READINESS_CHECK")) is None


def test_whisper_service_flags_unsure_turns_and_records(tmp_path, monkeypatch):
    from agent import stt_whisper as W

    results = iter([
        {"text": "हाँ जी", "lang": "hi", "avg_logprob": -0.3, "compression_ratio": 1.1, "no_speech_prob": 0.01},
        {"text": "कर दो कर दो कर दो कर दो", "lang": "hi", "avg_logprob": -0.4, "compression_ratio": 3.1,
         "no_speech_prob": 0.02},
    ])
    monkeypatch.setattr(W, "transcribe_detailed", lambda x: next(results))
    monkeypatch.setattr(W, "RECORDINGS", tmp_path)
    svc = W.make_whisper_stt("CALL-T", record=True)

    async def noop(*a, **k):
        pass
    svc.start_processing_metrics = svc.stop_processing_metrics = noop

    async def run():
        pcm = b"\x00\x01" * 16000
        return [f async for f in svc.run_stt(pcm)]

    svc.start_turn()
    asyncio.run(run())
    assert not svc.turn_unsure
    asyncio.run(run())
    assert svc.turn_unsure and [h["text"] for h in svc.turn_heard][0] == "हाँ जी"
    rows = [json.loads(line) for line in (tmp_path / "CALL-T" / "segments.jsonl").read_text().splitlines()]
    assert [r["unsure"] for r in rows] == [False, True] and (tmp_path / "CALL-T" / "002.wav").exists()
    svc.start_turn()
    assert not svc.turn_unsure and svc.turn_heard == []
