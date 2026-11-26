import asyncio
import time

from pipecat.frames.frames import InterruptionFrame, TextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from agent.bot import GreetingGate


def _push(gate, monkeypatch, frame, direction=FrameDirection.DOWNSTREAM):
    """Feed one frame to the gate without a pipeline; return what it passed on."""
    async def noop(self, frame, direction): pass
    out = []

    async def record(f, d=FrameDirection.DOWNSTREAM): out.append(f)

    monkeypatch.setattr(FrameProcessor, "process_frame", noop)
    monkeypatch.setattr(gate, "push_frame", record)
    asyncio.run(gate.process_frame(frame, direction))
    return out


def test_interruption_inside_the_protection_window_is_dropped(monkeypatch):
    gate = GreetingGate(lambda: asyncio.sleep(0))
    gate.protect(5)
    assert _push(gate, monkeypatch, InterruptionFrame()) == []


def test_other_frames_and_upstream_interruptions_still_pass_while_protected(monkeypatch):
    gate = GreetingGate(lambda: asyncio.sleep(0))
    gate.protect(5)
    assert len(_push(gate, monkeypatch, TextFrame("x"))) == 1
    assert len(_push(gate, monkeypatch, InterruptionFrame(), FrameDirection.UPSTREAM)) == 1


def test_interruption_passes_when_unprotected_and_after_the_window(monkeypatch):
    gate = GreetingGate(lambda: asyncio.sleep(0))
    assert len(_push(gate, monkeypatch, InterruptionFrame())) == 1       # never protected
    gate.protect(0.05)
    time.sleep(0.1)
    assert len(_push(gate, monkeypatch, InterruptionFrame())) == 1       # window over
