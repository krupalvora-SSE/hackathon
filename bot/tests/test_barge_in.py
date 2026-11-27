"""Short answers are heard when the bot is quiet; talking over the bot needs sustained speech (agent/barge_in.py).

From the 2026-11-27 call: with Silero at 0.5 s / 0.8 the bot never heard "हाँ" after its third question.
"""
import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

from agent.barge_in import PatientBargeInStrategy


def _run(frames, hold=0.1):
    async def go():
        s = PatientBargeInStrategy(barge_in_secs=hold)
        events = []

        async def started(_s, _params):
            events.append("turn")

        async def reset(_s):
            events.append("dropped")

        s.add_event_handler("on_user_turn_started", started)
        s.add_event_handler("on_reset_aggregation", reset)
        for f in frames:
            if isinstance(f, float):
                await asyncio.sleep(f)
            else:
                await s.process_frame(f)
        await asyncio.sleep(hold + 0.05)
        await s.cleanup()
        return events

    return asyncio.run(go())


def _tx(text):
    return TranscriptionFrame(text=text, user_id="u", timestamp="t")


def test_short_answer_when_the_bot_is_quiet_starts_the_turn_at_once():
    assert _run([VADUserStartedSpeakingFrame()]) == ["turn"]


def test_brief_noise_over_the_bot_does_not_interrupt():
    events = _run([BotStartedSpeakingFrame(), VADUserStartedSpeakingFrame(), 0.03, VADUserStoppedSpeakingFrame(),
                   _tx("हाँ")])
    assert events == ["dropped"]


def test_sustained_speech_over_the_bot_interrupts():
    assert _run([BotStartedSpeakingFrame(), VADUserStartedSpeakingFrame(), 0.2]) == ["turn"]


def test_answer_that_overlaps_the_bots_last_words_is_kept():
    events = _run([BotStartedSpeakingFrame(), VADUserStartedSpeakingFrame(), 0.03, BotStoppedSpeakingFrame()])
    assert events == ["turn"]


def test_transcript_without_a_vad_start_still_counts_when_the_bot_is_quiet():
    assert _run([BotStartedSpeakingFrame(), BotStoppedSpeakingFrame(), _tx("जी")]) == ["turn"]
