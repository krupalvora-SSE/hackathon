"""When the customer's turn starts: at once when the bot is quiet, only after sustained speech over the bot.

One Silero setting can't do both jobs. A strict one (0.5 s, 0.8) stops echo from cutting the bot off but
never hears a short "हाँ" / "जी" / "नहीं" (they last under 0.5 s: 2026-11-27 call went deaf after the third
question). So Silero stays sensitive (vad_start_secs 0.2) and this strategy adds the strictness only
while the bot is talking: the customer must keep talking for barge_in_secs before the bot stops.
A transcript that arrives while the bot talks without such a turn (echo, a cough) is dropped.
"""
from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy


class PatientBargeInStrategy(BaseUserTurnStartStrategy):
    def __init__(self, *, barge_in_secs: float = 0.6, **kwargs):
        super().__init__(**kwargs)
        self._hold = barge_in_secs
        self._bot_speaking = False
        self._user_speaking = False
        self._pending: asyncio.Task | None = None

    async def handle_user_turn_started(self):
        self._cancel()

    async def cleanup(self):
        self._cancel()
        await super().cleanup()

    def _cancel(self):
        if self._pending:
            self._pending.cancel()
            self._pending = None

    async def _after_hold(self):
        await asyncio.sleep(self._hold)
        self._pending = None
        if self._user_speaking:
            await self.report_user_turn_started()

    async def process_frame(self, frame: Frame):
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._user_speaking and self._pending:  # the bot finished while they talked: their turn now
                self._cancel()
                await self.report_user_turn_started()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            if not self._bot_speaking:
                await self.report_user_turn_started()
            elif not self._pending:
                self._pending = asyncio.create_task(self._after_hold())
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
            self._cancel()
        elif isinstance(frame, TranscriptionFrame):
            if self._bot_speaking:
                await self.trigger_reset_aggregation()  # heard over the bot, too short to be a turn
            else:
                await self.report_user_turn_started()  # VAD missed the start: the transcript still counts
