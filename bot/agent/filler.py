"""Spoken filler: a short "जी…" when the reply is slow, so a silent gap doesn't feel like a dropped line."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

# "एक second" became "एक सेकंड", a word the Hindi voice never trained on: it came out Hindi-accented.
# "जी, देखता हूँ" garbled before "आपकी visit … confirm हो गई है" (CALL-7b3aafb3): "देखता" is not in the training
# lines either. Every filler must use only words the Hindi voice was trained on (voice_training/script_*.txt).
# Neutral on purpose: "हाँ जी…" sounded like agreement right before "माफ़ कीजिए, ठीक से सुनाई नहीं दिया".
# Varied short acknowledgements, so back-to-back slow turns don't repeat "एक मिनट रुकिए" (all trained words).
# Left out "बिल्कुल": it agrees, and can land right before "माफ़ कीजिए, उस दिन वह time खाली नहीं है".
# Left out "समझ गया।": it claims we understood, and doubled the "समझ गया।" that opens SITE_ON_HOLD / ASK_READY_DATE.
FILLERS = ["जी, एक मिनट।", "अच्छा…", "जी…"]


def _negative(text: str) -> bool:
    """A "no" / not-our-customer turn: "नहीं, मेरे घर पे कोई plant नहीं है" got "जी, एक मिनट।" then "कोई बात
    नहीं। आपको कौन सी तारीख…" (CALL-786626d2). Any filler there sounds like the bot didn't listen."""
    from . import normalize as N

    return N.parse_bool(text) is False or N.leaving_intent(text) is not None


class Filler:
    """Starts a timer when the customer stops talking; speaks one filler if the bot hasn't started
    its real reply by then. Any bot speech or new customer speech cancels it."""

    def __init__(self, speak: Callable[[str], Awaitable[None]], delay_s: float, enabled: bool = True,
                 skip_turns: int = 1, active: Callable[[], bool] = lambda: True, lines: list[str] = FILLERS):
        self._speak, self._delay, self._enabled, self._active, self._lines = speak, delay_s, enabled, active, lines
        self._skip = skip_turns            # the first reply is the greeting, which has its own pacing
        self._n = 0
        self._task: asyncio.Task | None = None

    def customer_stopped(self, unsure: bool = False, text: str = "") -> None:
        """unsure: Whisper doubted what it heard, so the reply will be "माफ़ कीजिए, ठीक से सुनाई नहीं दिया".
        A filler first sounds like the bot understood (CALL-392c194e: heard as "बढ़िया" before the sorry).
        text: what the customer said; no filler after a "no" or a not-our-customer turn."""
        self.cancel()
        if self._skip > 0:
            self._skip -= 1
        elif self._enabled and not unsure and not _negative(text):
            self._task = asyncio.create_task(self._after_delay())

    def cancel(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _after_delay(self) -> None:
        await asyncio.sleep(self._delay)
        self._task = None
        if self._active():
            line = self._lines[self._n % len(self._lines)]
            self._n += 1
            await self._speak(line)
