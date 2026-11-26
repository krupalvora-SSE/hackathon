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

from .events import Emitter, save_result
from .engine import make_engine
from .prompts import HANDOVER_BROWSER_NOTE
from .tools import Action

# Rough INR rates for the cost-per-call estimate; override in .env with real contract prices.
RATE = {
    "stt_per_min": float(os.getenv("COST_STT_INR_PER_MIN", "0.5")),
    "tts_per_1k_chars": float(os.getenv("COST_TTS_INR_PER_1K_CHARS", "1.5")),
    "llm_per_m_in": float(os.getenv("COST_LLM_INR_PER_M_IN", "13")),
    "llm_per_m_out": float(os.getenv("COST_LLM_INR_PER_M_OUT", "51")),
    "tel_per_min": float(os.getenv("COST_TELEPHONY_INR_PER_MIN", "0.6")),
}
SILENCE_SECS = float(os.getenv("SILENCE_SECS", "6"))
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


def _llm() -> OpenAILLMService:
    return OpenAILLMService(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.getenv("LLM_BASE_URL") or None,
        settings=OpenAILLMService.Settings(
            model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
            temperature=0.3,
            max_tokens=120,
        ),
    )


async def run_bot(transport, *, call_id: str, booking: dict, telephony: bool, sample_rate: int,
                  call_sid: str | None = None) -> None:
    emitter = Emitter(call_id)
    engine = make_engine(call_id, booking, on_event=emitter.emit)  # CALL_FLOW picks YAML flow or checklist
    logger.info(f"call {call_id} booking={booking.get('id')} telephony={telephony} flow={engine.name}")

    stt = SarvamRealtimeSTTService(
        api_key=os.environ["SARVAM_API_KEY"],
        keyterms=KEYTERMS,
        settings=SarvamRealtimeSTTService.Settings(
            model=os.getenv("SARVAM_STT_MODEL", "saaras:v4"),
            language=Language.HI_IN,
            stream_type="fast",
            mode="codemix",
        ),
    )
    tts = SarvamTTSService(
        api_key=os.environ["SARVAM_API_KEY"],
        sample_rate=sample_rate,
        settings=SarvamTTSService.Settings(
            model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v4-flash"),
            voice=os.getenv("SARVAM_TTS_VOICE") or None,
            language=Language.HI_IN,
        ),
    )
    llm = _llm()

    tools = ToolsSchema(standard_tools=[
        FunctionSchema(name=t["name"], description=t["description"], properties=t["properties"],
                       required=t["required"]) for t in engine.tools
    ])
    context = LLMContext(messages=[{"role": "system", "content": engine.system_prompt()}], tools=tools)
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_idle_timeout=SILENCE_SECS,
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
        idle_timeout_secs=120,
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
