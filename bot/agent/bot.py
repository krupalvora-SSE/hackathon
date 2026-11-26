"""Pipecat pipeline: transport in -> Sarvam STT -> LLM (tools) -> Sarvam TTS -> transport out.

The same run_bot() serves Exotel (8 kHz phone) and SmallWebRTC (browser fallback).
"""
from __future__ import annotations

import os
import statistics
import time

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndFrame,
    Frame,
    FunctionCallResultProperties,
    MetricsFrame,
    TTSSpeakFrame,
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
from pipecat.services.sarvam.stt import SarvamRealtimeSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy, VADUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies, default_user_turn_stop_strategies

from . import config as call_config
from .events import Emitter, save_result
from .engine import make_engine
from .prompts import HANDOVER_BROWSER_NOTE
from .tools import Action
from .voice import SarvamCloneTTSService, active_voice_id

# Rough INR rates for the cost-per-call estimate; override in .env with real contract prices.
RATE = {
    "stt_per_min": float(os.getenv("COST_STT_INR_PER_MIN", "0.5")),
    "tts_per_1k_chars": float(os.getenv("COST_TTS_INR_PER_1K_CHARS", "1.5")),
    "llm_per_m_in": float(os.getenv("COST_LLM_INR_PER_M_IN", "13")),
    "llm_per_m_out": float(os.getenv("COST_LLM_INR_PER_M_OUT", "51")),
    "tel_per_min": float(os.getenv("COST_TELEPHONY_INR_PER_MIN", "0.6")),
}
KEYTERMS = ["SolarSquare", "seedhi", "ladder", "light", "paani", "nal", "subah", "dopahar", "shaam", "tarikh",
            "Somvar", "Mangalvar", "Budhvar", "Guruvar", "Shukravar", "Shanivar", "haan", "nahi"]


class CallMetrics(FrameProcessor):
    """Sits at the end of the pipeline: per-turn voice-to-voice latency + usage for cost."""

    def __init__(self, emitter: Emitter, telephony: bool):
        super().__init__()
        self.emitter = emitter
        self.telephony = telephony
        self.started = time.monotonic()
        self.latencies: list[int] = []
        self._user_stopped_at: float | None = None
        self.llm_in = self.llm_out = self.tts_chars = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
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


def _turn_strategies(cfg: dict) -> UserTurnStrategies | None:
    """None keeps Sarvam's recommended strategies (its server VAD owns turns and barge-in)."""
    if cfg["endpointing"] == "sarvam":
        return None
    interrupt = cfg["barge_in"]
    return UserTurnStrategies(
        start=[VADUserTurnStartStrategy(enable_interruptions=interrupt),
               TranscriptionUserTurnStartStrategy(enable_interruptions=interrupt)],
        stop=default_user_turn_stop_strategies(),
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


async def _run_bot(transport, *, call_id: str, booking: dict, telephony: bool, sample_rate: int,
                   call_sid: str | None = None) -> None:
    emitter = Emitter(call_id)
    engine = make_engine(call_id, booking, on_event=emitter.emit)  # CALL_FLOW picks YAML flow or checklist
    logger.info(f"call {call_id} booking={booking.get('id')} telephony={telephony} flow={engine.name}")

    cfg = call_config.load()  # read per call: /voice page edits apply from the next call
    logger.info(f"call {call_id} config {cfg}")
    stt_vad = {}
    if cfg["endpointing"] == "sarvam":  # server-side VAD tuning; Sarvam rejects it in manual mode
        stt_vad = {k: v for k, v in {"threshold": cfg["stt_threshold"],
                                     "min_speech_duration_ms": cfg["stt_min_speech_ms"],
                                     "silence_duration_ms": cfg["stt_silence_ms"]}.items() if v is not None}
    stt = SarvamRealtimeSTTService(
        api_key=os.environ["SARVAM_API_KEY"],
        keyterms=KEYTERMS,
        endpointing="vad" if cfg["endpointing"] == "sarvam" else "manual",
        should_interrupt=cfg["barge_in"],
        settings=SarvamRealtimeSTTService.Settings(
            model=cfg["stt_model"],
            language=Language.HI_IN,
            stream_type=cfg["stt_stream_type"],
            mode="codemix",
            **stt_vad,
        ),
    )
    tts_extra = {k: cfg[f"tts_{k}"] for k in ("pace", "pitch", "loudness") if cfg[f"tts_{k}"] is not None}
    tts_settings = SarvamTTSService.Settings(
        model=cfg["tts_model"],
        voice=cfg["tts_voice"] or None,
        language=Language.HI_IN,
        min_buffer_size=cfg["tts_min_buffer"],
        max_chunk_length=cfg["tts_max_chunk"],
        **tts_extra,
    )
    if clone_id := active_voice_id():  # team member's cloned voice (see /voice page)
        logger.info(f"call {call_id} TTS voice: cloned {clone_id}")
        tts = SarvamCloneTTSService(api_key=os.environ["SARVAM_API_KEY"], sample_rate=sample_rate,
                                    clone_voice_id=clone_id, settings=tts_settings)
    else:
        tts = SarvamTTSService(api_key=os.environ["SARVAM_API_KEY"], sample_rate=sample_rate,
                               settings=tts_settings)
    llm = _llm(cfg)

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
            **({"user_turn_strategies": ts} if (ts := _turn_strategies(cfg)) else {}),
        ),
    )
    metrics = CallMetrics(emitter, telephony)

    pipeline = Pipeline([
        transport.input(), stt, aggregators.user(), llm, tts, transport.output(),
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
        save_result(record)
        emitter.emit(metrics.metrics())
        emitter.emit({"type": "result", "record": record})
        emitter.emit({"type": "status", "status": "ended", "outcome": engine.outcome})
        logger.info(f"call {call_id} ended ({reason}): {record}")
        await emitter.close()

    async def perform(action: Action) -> None:
        frames: list[Frame] = [TTSSpeakFrame(t, append_to_context=True) for t in action.speak]
        if action.end:
            if engine.outcome == "handed_to_human" and not telephony:
                emitter.emit({"type": "transcript", "role": "agent", "text": HANDOVER_BROWSER_NOTE})
            frames.append(EndFrame())  # queued after the goodbye, so it is spoken in full
        if frames:
            await worker.queue_frames(frames)

    async def handle_tool(params: FunctionCallParams) -> None:
        args = dict(params.arguments or {})
        action = engine.dispatch(params.function_name, args)
        logger.info(f"tool {params.function_name}({args}) -> {action.result}")
        await perform(action)
        await params.result_callback(
            action.result, properties=FunctionCallResultProperties(run_llm=action.run_llm))

    for t in engine.tools:
        llm.register_function(t["name"], handle_tool, cancel_on_interruption=True)

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def _user_said(_agg, _strategy, message):
        if getattr(message, "content", None):
            engine.heard_customer()
            emitter.emit({"type": "transcript", "role": "customer", "text": message.content})

    @aggregators.user().event_handler("on_user_turn_idle")
    async def _silence(_agg):
        if engine.outcome:
            return
        await perform(engine.on_silence())

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def _agent_said(_agg, message):
        if getattr(message, "content", None):
            emitter.emit({"type": "transcript", "role": "agent", "text": message.content})

    @transport.event_handler("on_client_connected")
    async def _connected(_t, _client):
        status = {"type": "status", "status": "in_progress"}
        if call_sid:
            status["call_sid"] = call_sid
        emitter.emit(status)
        if engine.outcome:  # blocked before dialling (DNC / allowlist / hours): say nothing, hang up
            await worker.queue_frames([EndFrame()])
            return
        await worker.queue_frames([TTSSpeakFrame(t, append_to_context=True) for t in engine.opening()])

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
