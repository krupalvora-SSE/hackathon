import asyncio

from agent import config
from agent.filler import FILLERS, Filler


def run(scenario, **kw):
    """Run `scenario(filler)` against a Filler with a 50 ms delay; return what was spoken."""
    spoken = []

    async def main():
        async def speak(line): spoken.append(line)
        f = Filler(speak, 0.05, skip_turns=kw.pop("skip_turns", 0), **kw)
        await scenario(f)
        await asyncio.sleep(0.15)

    asyncio.run(main())
    return spoken


def test_slow_reply_gets_one_filler():
    async def s(f): f.customer_stopped()
    assert run(s) == [FILLERS[0]]


def test_fast_reply_gets_none():
    async def s(f):
        f.customer_stopped()
        await asyncio.sleep(0.01)
        f.cancel()                      # the bot started its real reply
    assert run(s) == []


def test_customer_talking_again_cancels():
    async def s(f):
        f.customer_stopped()
        await asyncio.sleep(0.01)
        f.cancel()
        await asyncio.sleep(0.1)
    assert run(s) == []


def test_first_turn_is_skipped_then_filler_resumes():
    async def s(f):
        f.customer_stopped()            # the greeting reply
        await asyncio.sleep(0.1)
        f.customer_stopped()
    assert run(s, skip_turns=1) == [FILLERS[0]]


def test_lines_rotate():
    async def s(f):
        for _ in range(2):
            f.customer_stopped()
            await asyncio.sleep(0.1)
    assert run(s) == FILLERS[:2]


def test_disabled_and_ended_calls_stay_silent():
    async def s(f): f.customer_stopped()
    assert run(s, enabled=False) == []
    assert run(s, active=lambda: False) == []


def test_settings_have_defaults():
    cfg = config.load()
    assert cfg["filler"] is True and cfg["filler_delay_ms"] == 1000


def test_filler_is_cancelled_when_the_llm_starts_replying():
    from pipecat.frames.frames import LLMFullResponseStartFrame
    from pipecat.tests.utils import run_test

    from agent.bot import CallMetrics

    class Quiet:
        def emit(self, _): pass

    cancelled = []
    metrics = CallMetrics(Quiet(), False, on_bot_started=lambda: cancelled.append(1))
    asyncio.run(run_test(metrics, frames_to_send=[LLMFullResponseStartFrame()]))
    assert cancelled
