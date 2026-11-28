"""Pipecat pipeline: transport in -> local STT -> LLM (tools) -> local Piper TTS -> transport out.

Speech recognition (Whisper) and the voice (Piper) run on this machine from local model files.

run_bot() serves SmallWebRTC browser calls (telephony=True models an 8 kHz phone leg for evals).
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from datetime import date

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    InterruptionFrame,
    Frame,
    FunctionCallResultProperties,
    LLMContextFrame,
    LLMFullResponseStartFrame,
    MetricsFrame,
    TTSSpeakFrame,
    UserIdleTimeoutUpdateFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.turns.user_turn_strategies import TurnAnalyzerUserTurnStopStrategy, UserTurnStrategies

from . import config as call_config
from . import normalize as N
from .barge_in import PatientBargeInStrategy
from .events import Emitter, save_result
from .engine import make_engine
from .flow import band_from_range, parse_band
from .filler import Filler
from .knowledge import fetch_knowledge
from .prompts import HANDOVER_BROWSER_NOTE
from .recording import CallRecorder
from .tools import Action
from .transcript import CallTranscript

# Rough INR rates for the cost-per-call estimate; override in .env with real contract prices.
RATE = {
    "stt_per_min": float(os.getenv("COST_STT_INR_PER_MIN", "0")),  # local STT
    "tts_per_1k_chars": float(os.getenv("COST_TTS_INR_PER_1K_CHARS", "0")),  # local Piper
    "llm_per_m_in": float(os.getenv("COST_LLM_INR_PER_M_IN", "13")),
    "llm_per_m_out": float(os.getenv("COST_LLM_INR_PER_M_OUT", "51")),
    "tel_per_min": float(os.getenv("COST_TELEPHONY_INR_PER_MIN", "0.6")),
}
YIELD_LINES = ["जी, sorry, आप बोलिए।", "ओह sorry जी, please go ahead।", "जी जी, बोलिए।"]


class FillerTap(FrameProcessor):
    """Sits between the LLM and TTS so a filler reaches the voice while the LLM is still thinking. Queued at the
    pipeline's start it waited behind the busy LLM and was spoken glued to the real reply (CALL-2c3e81bb)."""

    async def say(self, text: str) -> None:
        await self.queue_frame(TTSSpeakFrame(text, append_to_context=False))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class GreetingGate(FrameProcessor):
    """The bot opens with "Hello?". The customer's first reply is held back from the LLM and the
    real greeting is spoken instead (on_first_reply)."""

    def __init__(self, on_first_reply, stt=None):
        super().__init__()
        self._on_first_reply = on_first_reply
        self._stt = stt
        self.greeted = False
        self._protected_until = 0.0

    async def greet(self) -> None:
        """Speak the greeting in answer to the first reply (or to silence). That reply never reaches the LLM, so
        it is done with here: a misheard "Hello" ("प्रोग") must not make the next clear "हाँ" unsure and asked
        again (CALL-1e03b45f)."""
        self.greeted = True
        if self._stt is not None and hasattr(self._stt, "consume_turn"):
            self._stt.consume_turn()
        await self._on_first_reply()

    def protect(self, secs: float) -> None:
        """Ignore interruptions for `secs`: early line noise must not cancel "Hello?" before it is heard."""
        self._protected_until = time.monotonic() + secs

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (isinstance(frame, InterruptionFrame) and direction == FrameDirection.DOWNSTREAM
                and time.monotonic() < self._protected_until):
            return
        if isinstance(frame, LLMContextFrame) and not self.greeted:
            await self.greet()
            return
        await self.push_frame(frame, direction)


class FastPath(FrameProcessor):
    """Sits after the GreetingGate, before the LLM. A plain turn (agent/fastpath.py: "हाँ जी", "नहीं",
    "सुबह 9 से 12") is acted on here and never reaches the LLM, saving its ~1.5 s; everything else passes on."""

    def __init__(self, engine, stt, act, enabled: bool = True):
        super().__init__()
        self._engine, self._stt, self._act, self.enabled = engine, stt, act, enabled

    def _args(self, context) -> dict | None:
        call = getattr(self._engine, "call", None)
        if not self.enabled or call is None:
            return None
        from .fastpath import fast_intent

        heard = " ".join(h["text"] for h in getattr(self._stt, "turn_heard", []))
        if not heard:
            last = context.get_messages()[-1] if context.get_messages() else {}
            heard = last.get("content", "") if isinstance(last, dict) and last.get("role") == "user" else ""
        return fast_intent(call, heard if isinstance(heard, str) else "", getattr(self._stt, "turn_unsure", False))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and (args := self._args(frame.context)):
            action = await self._act("customer_said", args, fast=True)
            # Keep the LLM's history as if it had made this call: it learns the step from the tool result.
            call_id = f"fast-{time.monotonic_ns()}"
            frame.context.add_messages([
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "customer_said", "arguments": json.dumps(args, ensure_ascii=False)}}]},
                {"role": "tool", "tool_call_id": call_id, "content": json.dumps(action.result, ensure_ascii=False)},
            ])
            if action.run_llm:   # e.g. an error result: let the LLM handle it as it would its own call
                await self.push_frame(frame, direction)
            return
        await self.push_frame(frame, direction)


class CallMetrics(FrameProcessor):
    """Sits at the end of the pipeline: per-turn voice-to-voice latency + usage for cost."""

    def __init__(self, emitter: Emitter, telephony: bool, on_cut_in=None, on_bot_started=None):
        super().__init__()
        self.emitter = emitter
        self.telephony = telephony
        self._on_cut_in = on_cut_in
        self._on_bot_started = on_bot_started
        self._bot_speaking = False
        self.started = time.monotonic()
        self.latencies: list[int] = []
        self._user_stopped_at: float | None = None
        self.llm_in = self.llm_out = self.tts_chars = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            if self._on_bot_started:
                self._on_bot_started()
            self._speaking_since = time.monotonic()
        elif isinstance(frame, LLMFullResponseStartFrame) and self._on_bot_started:
            self._on_bot_started()  # the reply is being generated: too late for a filler
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif (isinstance(frame, InterruptionFrame) and self._bot_speaking and self._on_cut_in
              and time.monotonic() - self._speaking_since >= 1.0):  # not a clipped first word
            self._on_cut_in()  # the customer cut the bot off mid-sentence
        if isinstance(frame, (VADUserStoppedSpeakingFrame, UserStoppedSpeakingFrame)):
            self._user_stopped_at = self._user_stopped_at or time.monotonic()
        elif isinstance(frame, BotStartedSpeakingFrame) and self._user_stopped_at:
            ms = int((time.monotonic() - self._user_stopped_at) * 1000)
            self._user_stopped_at = None
            self.latencies.append(ms)
            self.emitter.emit({"type": "latency", "ms": ms})
            self.emitter.emit(self.metrics())
        elif isinstance(frame, MetricsFrame):
            for d in frame.data:
                if isinstance(d, LLMUsageMetricsData):
                    self.llm_in += d.value.prompt_tokens or 0
                    self.llm_out += d.value.completion_tokens or 0
                elif isinstance(d, TTSUsageMetricsData):
                    self.tts_chars += d.value or 0
        await self.push_frame(frame, direction)

    def metrics(self) -> dict:
        secs = time.monotonic() - self.started
        mins = secs / 60
        cost = (mins * RATE["stt_per_min"]  # streaming STT bills the whole stream
                + self.tts_chars / 1000 * RATE["tts_per_1k_chars"]
                + self.llm_in / 1e6 * RATE["llm_per_m_in"] + self.llm_out / 1e6 * RATE["llm_per_m_out"]
                + (mins * RATE["tel_per_min"] if self.telephony else 0))
        return {"type": "metrics", "stt_seconds": round(secs, 1), "llm_input_tokens": self.llm_in,
                "llm_output_tokens": self.llm_out, "tts_chars": self.tts_chars,
                "call_seconds": round(secs, 1), "cost_inr": round(cost, 2)}

    def latency_summary(self) -> dict:
        if not self.latencies:
            return {"p50": None, "p95": None, "turns": 0}
        s = sorted(self.latencies)
        return {"p50": int(statistics.median(s)), "p95": s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))],
                "turns": len(s)}


def _llm(cfg: dict):
    """google (Gemini) or openai (any OpenAI-compatible endpoint), per the /voice page settings."""
    if cfg["llm_provider"] == "google":
        from pipecat.services.google.llm import GoogleLLMService

        # Pipecat sends each model's lowest thinking setting by default (minimal for 3.6 flash).
        return GoogleLLMService(
            api_key=os.environ["GOOGLE_API_KEY"],
            settings=GoogleLLMService.Settings(
                model=cfg["llm_model"] or "gemini-3.6-flash",
                temperature=cfg["llm_temperature"],
                max_tokens=cfg["llm_max_tokens"],
            ),
        )
    return OpenAILLMService(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.getenv("LLM_BASE_URL") or None,
        settings=OpenAILLMService.Settings(
            model=cfg["llm_model"] or "gpt-4.1-mini",
            temperature=cfg["llm_temperature"],
            max_tokens=cfg["llm_max_tokens"],
        ),
    )


# Steps where a "yes" writes to Beat Plan (books the slot / pauses the site): it must be a clear yes.
COMMIT_STATES = {"RECAP", "HOLD_CONSENT"}
KEEP_ON_UNSURE = {"unclear", "other_language", "repeat_request"}  # already the safe reading


def _leaving_heard(args: dict, stt, engine) -> str | None:
    """no_solar_plant / wrong_number when Whisper was sure and the words plainly say so, whatever the LLM read
    ("कोई plant है ही नहीं" came back as deny, not_serviceable, wrong_person in CALL-786626d2)."""
    if getattr(stt, "turn_unsure", False) or args.get("intent") in ("no_solar_plant", "wrong_number"):
        return None
    heard = " ".join(h["text"] for h in getattr(stt, "turn_heard", []))
    call = getattr(engine, "call", None)
    intent = N.leaving_intent(heard)
    allowed = call.spec.allowed_intents(call.state) if call is not None and hasattr(call, "spec") else ()
    return intent if intent and intent in allowed else None


def _offered_day_heard(args: dict, stt, engine) -> str | None:
    """At the day question, a turn we would otherwise ask again ("सुमार थीस लंड", CALL-418d20ee) that sounds
    like exactly one of the days just offered: that day (ISO). Never on a "no"; ASK_SLOT then names the
    day, so a wrong guess is heard and corrected."""
    call = getattr(engine, "call", None)
    if getattr(call, "state", None) != "ALTERNATE_DAYS" or not getattr(call, "offered", None):
        return None
    if args.get("intent") not in ("unclear", "affirm") or args.get("option") or args.get("date"):
        return None
    if args.get("intent") == "affirm" and not getattr(stt, "turn_unsure", False):
        return None
    heard = " ".join(h["text"] for h in getattr(stt, "turn_heard", []))
    if not heard or N.parse_bool(heard) is False:
        return None
    d = N.match_offered_day(heard, [date.fromisoformat(s["date"]) for s in call.offered])
    return d.isoformat() if d else None


def _opens_with_yes(heard: str) -> bool:
    """An unsure turn that still starts with a plain yes and has no "no" ("येस, है." / "हाँ मिल जाएगा" heard
    as "हाँ मिजया लाँ", CALL-5b614da9). Enough outside the booking steps: RECAP reads every answer back."""
    words = N._clean(heard).split()
    return bool(words) and words[0] in N.CLEAR_YES and N.parse_bool(heard) is not False \
        and not set(words) & {"मत", "mat", "शायद", "shayad", "maybe", "पता"}


IDENTITY_STATES = {"GREET"}


def _identity_yes_heard(args: dict, stt, engine) -> bool:
    """At "क्या Kavita जी बोल रहे हैं?" the LLM gave up on a garbled reply that still opens with a clear yes
    ("हाँ, सोल पालोग?" CALL-24b72b22, "हाँ बुल रिम।" CALL-392c194e: "हाँ, बोल रहा हूँ"). Take the yes: a wrong
    person says "नहीं", and the next line names the date anyway."""
    call = getattr(engine, "call", None)
    if args.get("intent") != "unclear" or getattr(call, "state", None) not in IDENTITY_STATES:
        return False
    heard = " ".join(h["text"] for h in getattr(stt, "turn_heard", []))
    return _opens_with_yes(heard)


def _same_reading_again(args: dict, state: str | None, engine) -> bool:
    """Outside the booking steps, the LLM read an unsure turn the same way as the unsure turn just before it
    at the same step: take it rather than ask a third time and hand the call to a senior (CALL-5b614da9)."""
    key = (state, args.get("intent"), repr(sorted((k, repr(v)) for k, v in args.items() if k != "language")))
    prev, engine._last_unsure_reading = getattr(engine, "_last_unsure_reading", None), key
    return state not in COMMIT_STATES and prev == key


def _yes_and_confirm(heard: str) -> bool:
    """An unsure transcript that still plainly says yes: it opens with a yes word and also says "confirm"
    ("हाँ, confirm पुटू" in CALL-fc2f980f was asked twice at RECAP). Two separate yes signals, no "no"."""
    words = N._clean(heard).split()
    confirm = {"confirm", "कन्फर्म", "कन्फ़र्म"}
    return (bool(words) and words[0] in N.CLEAR_YES - confirm and N.parse_bool(heard) is not False
            and not set(words) & {"मत", "mat", "मात", "रुको", "ruko", "wait"}
            and bool(set(words[1:]) & confirm))


def _offered_band_heard(args: dict, heard: str, call) -> bool:
    """An unsure transcript that is still safe to act on: at ASK_SLOT the customer repeated one of the windows
    just offered ("नौ से बारह" heard as "नो से बारा"). Code, not the LLM, must find both ends of exactly one
    offered window in what Whisper heard, and the LLM's band must say the same."""
    if args.get("intent") != "gives_band" or getattr(call, "state", None) != "ASK_SLOT":
        return False
    band = band_from_range(heard, call.policies)
    return bool(band) and band in call._free_bands() and parse_band(str(args.get("band") or heard), call.policies) == band


def _ask_again(args: dict, stt, engine) -> str | None:
    """Why the customer should be asked again instead of acting on the LLM's reading, or None.

    Precision over speed: a misheard answer that books the wrong slot costs more than one re-ask.
    """
    intent = args.get("intent")
    if intent in KEEP_ON_UNSURE:
        return None
    call = getattr(engine, "call", None)
    state = getattr(call, "state", None)
    heard = " ".join(h["text"] for h in getattr(stt, "turn_heard", []))
    if not getattr(stt, "turn_unsure", False):
        engine._last_unsure_reading = None
    if getattr(stt, "turn_unsure", False) and not _offered_band_heard(args, heard, call) \
            and not (intent == "affirm" and _yes_and_confirm(heard)) \
            and not (state not in COMMIT_STATES and intent == "affirm" and _opens_with_yes(heard)) \
            and not _same_reading_again(args, state, engine):
        return "Whisper unsure of the transcript"
    if state in COMMIT_STATES and intent == "affirm" and heard and not N.clear_yes(heard):
        return f"no clear yes at {state}"
    return None


def _once(handler):
    """Run an async event handler on its first call only; later calls do nothing."""
    done = False

    async def wrapper(*args, **kwargs):
        nonlocal done
        if done:
            return None
        done = True
        return await handler(*args, **kwargs)

    return wrapper


def _turn_strategies(cfg: dict) -> UserTurnStrategies:
    """Local Silero VAD + Smart Turn decide when the customer starts and stops (Whisper doesn't stream);
    talking over the bot needs barge_in_secs of speech (agent/barge_in.py). Smart Turn waits at most
    turn_stop_secs when it thinks a short Hindi reply is unfinished."""
    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

    return UserTurnStrategies(
        start=[PatientBargeInStrategy(barge_in_secs=cfg["barge_in_secs"], enable_interruptions=cfg["barge_in"])],
        stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3(
            params=SmartTurnParams(stop_secs=cfg["turn_stop_secs"])))],
    )


async def run_bot(transport, *, call_id: str, **kwargs) -> None:
    """Runs a call; any crash (e.g. a missing API key) is reported to the dashboard instead of
    dying silently in a background task and leaving the call stuck on 'Starting'."""
    try:
        await _run_bot(transport, call_id=call_id, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"call {call_id} failed")
        emitter = Emitter(call_id)
        emitter.emit({"type": "status", "status": "failed", "error": f"{type(e).__name__}: {e}"})
        await emitter.close()


def _piper_tts(cfg: dict, call_id: str, sample_rate: int | None = None):
    """Local open-source TTS (Piper, male Hindi + English voices), optionally re-voiced as the local sample
    ("piper_clone", agent/voice_local.py). Raises if Piper can't load: there is no other voice."""
    from pipecat.services.piper.tts import PiperTTSService

    from .devanagari import DEVANAGARI_TEXT_TRANSFORMS

    from .voice_local import BilingualPiperTTSService

    from pipecat.services.piper.tts import PIPER_CACHE_DIR

    for voice in filter(None, (cfg["piper_voice"], cfg.get("piper_voice_en"))):  # local files only: never download
        if not (PIPER_CACHE_DIR / f"{voice}.onnx").exists():
            raise RuntimeError(f"Piper voice {voice} is not on this machine: copy {voice}.onnx and "
                               f"{voice}.onnx.json into {PIPER_CACHE_DIR}")
    pace = cfg["tts_pace"]
    settings = PiperTTSService.Settings(voice=cfg["piper_voice"], length_scale=round(1 / pace, 3) if pace else None,
                                        noise_scale=cfg["piper_noise_scale"], noise_w_scale=cfg["piper_noise_w"])
    try:
        tts = BilingualPiperTTSService(en_voice=cfg.get("piper_voice_en") or None,
                                       clone=cfg["tts_provider"] == "piper_clone", tau=cfg.get("clone_tau", 0.22),
                                       settings=settings, sample_rate=sample_rate,
                                       text_transforms=DEVANAGARI_TEXT_TRANSFORMS)
    except Exception as e:  # missing package, voice download failed
        raise RuntimeError(f"Piper voice {cfg['piper_voice']} unavailable: {e}") from e
    mine = " + my voice (local converter)" if cfg["tts_provider"] == "piper_clone" else ""
    logger.info(f"call {call_id} TTS voice: Piper {cfg['piper_voice']} / {cfg.get('piper_voice_en')}{mine}")
    return tts


async def _run_bot(transport, *, call_id: str, booking: dict, telephony: bool, sample_rate: int,
                   call_sid: str | None = None) -> None:
    emitter = Emitter(call_id)
    knowledge = await fetch_knowledge()      # answers and phrases an admin taught on the Questions view
    engine = make_engine(call_id, booking, on_event=emitter.emit, knowledge=knowledge)  # CALL_FLOW picks YAML flow or checklist
    logger.info(f"call {call_id} booking={booking.get('id')} telephony={telephony} flow={engine.name}")

    cfg = call_config.load()  # read per call: /voice page edits apply from the next call
    from .stt_whisper import make_whisper_stt, refresh as whisper_refresh, status as whisper_status, warm_up

    if not whisper_status()["ready"]:
        raise RuntimeError(f"no local STT available: {whisper_status()['message']}")
    await asyncio.to_thread(warm_up)  # no-op once loaded
    asyncio.get_running_loop().run_in_executor(None, whisper_refresh)  # page weights back in during the opener
    logger.info(f"call {call_id} config {cfg}")
    stt = make_whisper_stt(call_id, record=cfg["record_stt_audio"], name=(booking or {}).get("customerName"))
    tts = _piper_tts(cfg, call_id, sample_rate)
    await tts.warm(engine.opener())  # cold ONNX run / converter calibration must not delay the opener
    llm = _llm(cfg)
    spoken = CallTranscript(call_id)  # Bot / Human lines as actually spoken (bot/recordings/<call>/transcript.*)
    tts.on_spoken = spoken.bot

    tools = ToolsSchema(standard_tools=[
        FunctionSchema(name=t["name"], description=t["description"], properties=t["properties"],
                       required=t["required"]) for t in engine.tools
    ])
    context = LLMContext(messages=[{"role": "system", "content": engine.system_prompt()}], tools=tools)
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(
                confidence=cfg["vad_confidence"], start_secs=cfg["vad_start_secs"],
                stop_secs=cfg["vad_stop_secs"], min_volume=cfg["vad_min_volume"])),
            user_idle_timeout=cfg["silence_secs"],
            user_turn_stop_timeout=cfg["turn_stop_timeout"],
            user_turn_strategies=_turn_strategies(cfg),
        ),
    )
    yielded = {"at": 0.0, "n": 0, "turns": 0, "answered": False, "task": None}
    turn_over = asyncio.Event()

    async def yield_if_unanswered() -> None:
        # Wait for the customer's turn to end. If they actually answered, the normal reply covers it
        # (a "sorry, go ahead" on top would stack two acknowledgements). Only yield on a non-answer.
        try:
            await asyncio.wait_for(turn_over.wait(), timeout=5)
        except asyncio.TimeoutError:
            return
        if yielded["answered"] or engine.outcome:
            return
        yielded["at"] = time.monotonic()
        line = YIELD_LINES[yielded["n"] % len(YIELD_LINES)]
        yielded["n"] += 1
        await worker.queue_frames([TTSSpeakFrame(line, append_to_context=False)])

    def yield_floor() -> None:
        """Customer cut in mid-sentence (at most every 15 s, never during the hello exchange)."""
        if (engine.outcome or yielded["turns"] < 2 or time.monotonic() - yielded["at"] < 15
                or os.getenv("YIELD_ON_INTERRUPT", "1") == "0"):
            return
        if yielded["task"]:
            yielded["task"].cancel()
        turn_over.clear()
        yielded["answered"] = False
        yielded["task"] = asyncio.create_task(yield_if_unanswered())

    filler_tap = FillerTap()

    async def say_filler(line: str) -> None:
        await filler_tap.say(line)

    filler = Filler(say_filler, cfg["filler_delay_ms"] / 1000, enabled=cfg["filler"],
                    active=lambda: not engine.outcome)
    def cut_in() -> None:
        spoken.interrupted()
        yield_floor()

    metrics = CallMetrics(emitter, telephony, on_cut_in=cut_in, on_bot_started=filler.cancel)

    async def speak_opening() -> None:
        await worker.queue_frames([TTSSpeakFrame(t, append_to_context=True) for t in engine.opening()])

    gate = GreetingGate(speak_opening, stt)
    # `act` is defined further down (it needs `perform` and the pending-ask state): look it up when called
    fast_path = FastPath(engine, stt, lambda *a, **k: act(*a, **k), enabled=cfg["fast_path"])
    recorder = CallRecorder(call_id, sample_rate) if cfg["record_call"] else None
    pipeline = Pipeline([
        transport.input(), stt, aggregators.user(), gate, fast_path, llm, filler_tap, tts,
        transport.output(),
        *([recorder.processor] if recorder else []),  # after output: hears the customer and the bot
        aggregators.assistant(), metrics,
    ])
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=sample_rate, audio_out_sample_rate=sample_rate,
                              enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=cfg["idle_timeout"],
    )
    finished = False

    async def finish(reason: str) -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        engine.hang_up()  # no-op if the call already reached an outcome; flows still log to Beat Plan
        record = {**engine.record(), "latency": metrics.latency_summary(),
                  "cost_inr": metrics.metrics()["cost_inr"], "mode": "phone" if telephony else "browser"}
        if call_sid:
            record["call_sid"] = call_sid
        if recorder:
            await recorder.stop()  # writes the WAV now, so the record can point at it
            if recorder.saved():
                record["recording"] = {"url": f"/api/recordings/{call_id}.wav", "seconds": recorder.seconds,
                                       "channels": "left: customer, right: bot"}
        if spoken.save():
            record["transcript"] = {"url": f"/api/transcripts/{call_id}", "lines": len(spoken.lines)}
        save_result(record)
        emitter.emit(metrics.metrics())
        emitter.emit({"type": "result", "record": record})
        emitter.emit({"type": "status", "status": "ended", "outcome": engine.outcome})
        logger.info(f"call {call_id} ended ({reason}): {record}")
        await emitter.close()

    on_hold = {"on": False}

    async def end_hold() -> None:
        """Back to the normal silence timeout once the customer is back (or the hold ran out)."""
        if on_hold["on"]:
            on_hold["on"] = False
            await worker.queue_frames([UserIdleTimeoutUpdateFrame(timeout=cfg["silence_secs"])])

    async def perform(action: Action) -> None:
        frames: list[Frame] = [TTSSpeakFrame(t, append_to_context=True) for t in action.speak]
        if action.idle_timeout:  # "ek minute ruko": wait longer before the first reprompt
            on_hold["on"] = True
            frames.append(UserIdleTimeoutUpdateFrame(timeout=action.idle_timeout))
        if action.end:
            if engine.outcome == "handed_to_human" and not telephony:
                emitter.emit({"type": "transcript", "role": "agent", "text": HANDOVER_BROWSER_NOTE})
            frames.append(EndFrame())  # queued after the goodbye, so it is spoken in full
        if frames:
            await worker.queue_frames(frames)

    # Checklist mode: one LLM response can hold several save_slot calls (date + slot together).
    # Each asks for the next question; speak it once, just after the batch, using the latest one.
    # Cancelled if any call in the batch wants the LLM to respond instead (e.g. a validation error).
    pending_ask: dict = {"text": None, "task": None}

    async def speak_next() -> None:
        await asyncio.sleep(0.05)
        text, pending_ask["text"], pending_ask["task"] = pending_ask["text"], None, None
        if text and not engine.outcome:
            await worker.queue_frames([TTSSpeakFrame(text, append_to_context=True)])

    last_said = {"text": ""}                 # the customer's latest words, for the Questions view

    async def handle_tool(params: FunctionCallParams) -> None:
        filler.cancel()  # the LLM has answered; the real line follows in a moment
        args = dict(params.arguments or {})
        if params.function_name == "customer_said" and (leaving := _leaving_heard(args, stt, engine)):
            logger.info(f"call {call_id} {leaving} read by code: heard {stt.turn_heard}, LLM said {args}")
            args = {"intent": leaving, "language": args.get("language", "hi")}
        elif params.function_name == "customer_said" and (guess := _offered_day_heard(args, stt, engine)):
            logger.info(f"call {call_id} day matched by sound: heard {stt.turn_heard}, LLM said {args} -> {guess}")
            args = {"intent": "proposes_date", "date": guess, "language": args.get("language", "hi")}
        elif params.function_name == "customer_said" and _identity_yes_heard(args, stt, engine):
            logger.info(f"call {call_id} identity yes by its opening word: heard {stt.turn_heard}, LLM said {args}")
            args = {"intent": "affirm", "language": args.get("language", "hi")}
        elif params.function_name == "customer_said" and (why := _ask_again(args, stt, engine)):
            logger.info(f"call {call_id} asking again ({why}): heard {stt.turn_heard}, LLM said {args}")
            # keep the call's language: a garbled turn read as English ("Yes, I'm from India.") must not
            # switch the re-ask to English
            args = {"intent": "unclear", "language": getattr(getattr(engine, "call", None), "lang", None)
                    or args.get("language", "hi")}
        action = await act(params.function_name, args)
        await params.result_callback(
            action.result, properties=FunctionCallResultProperties(run_llm=action.run_llm))

    async def act(name: str, args: dict, fast: bool = False) -> Action:
        """Apply one customer_said reading (from the LLM, or from FastPath without it) and speak the result."""
        filler.cancel()
        if fast:
            engine._last_unsure_reading = None
        if name == "customer_said" and hasattr(stt, "consume_turn"):
            stt.consume_turn()
        if name == "customer_said" and args.get("intent") == "known_question" and not args.get("faq_id"):
            args["_said"] = last_said["text"]        # lets the flow pick the approved answer itself
        action = engine.dispatch(name, args)
        logger.info(f"tool {name}({args}){' [fast path, no LLM]' if fast else ''} -> {action.result}")
        if action.offguard and last_said["text"]:        # shown on the dashboard's Questions view for an admin
            emitter.emit({"type": "offguard", "kind": action.offguard, "text": last_said["text"],
                          "state": action.result.get("state"), "intent": args.get("intent")})
        if action.ask_next:
            pending_ask["text"] = action.ask_next
            if not pending_ask["task"]:
                pending_ask["task"] = asyncio.create_task(speak_next())
        elif pending_ask["task"] and (action.run_llm or action.speak):
            pending_ask["task"].cancel()
            pending_ask["text"] = pending_ask["task"] = None
        await perform(action)
        return action

    for t in engine.tools:
        llm.register_function(t["name"], handle_tool, cancel_on_interruption=True)

    @aggregators.user().event_handler("on_user_turn_started")
    async def _user_started(_agg, _strategy):
        filler.cancel()
        stt.start_turn()

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def _user_said(_agg, _strategy, message):
        if getattr(message, "content", None):
            last_said["text"] = message.content
        yielded["answered"] = bool(getattr(message, "content", None))
        turn_over.set()
        if getattr(message, "content", None):
            engine.heard_customer()
            await end_hold()
            filler.customer_stopped(unsure=getattr(stt, "turn_unsure", False), text=message.content)
            yielded["turns"] += 1
            spoken.human(message.content)
            emitter.emit({"type": "transcript", "role": "customer", "text": message.content})

    @aggregators.user().event_handler("on_user_turn_idle")
    async def _silence(_agg):
        if engine.outcome:
            return
        await end_hold()
        if not gate.greeted:  # no reply to "Hello?": give the greeting itself, not a second "Hello?"
            await gate.greet()
            return
        action = engine.on_silence()
        if not action.speak and action.idle_timeout:   # quiet wait after a re-ask: one more normal silence period
            action.idle_timeout = cfg["silence_secs"]
        await perform(action)

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def _agent_said(_agg, message):
        if getattr(message, "content", None):
            emitter.emit({"type": "transcript", "role": "agent", "text": message.content})

    @transport.event_handler("on_client_connected")
    @_once  # the browser transport can report one connection twice, 1 ms apart: "Hello?" twice (CALL-24b72b22)
    async def _connected(_t, _client):
        status = {"type": "status", "status": "in_progress"}
        if call_sid:
            status["call_sid"] = call_sid
        emitter.emit(status)
        if engine.outcome:  # blocked before dialling (DNC / allowlist / hours): say nothing, hang up
            await worker.queue_frames([EndFrame()])
            return
        gate.protect(float(os.getenv("OPENER_PROTECT_S", "2")))
        await worker.queue_frames([TTSSpeakFrame(engine.opener(), append_to_context=True)])

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(_t, _client):
        await finish("disconnected")
        await worker.cancel()

    emitter.emit({"type": "status", "status": "connecting"})
    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        await finish("pipeline_end")
