"""Bot server: SmallWebRTC offer (browser calls) + /voice settings page.

Run: uv run uvicorn server:app --port 7860
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
# Local models only: Whisper, Piper voices and Smart Turn load from files already on disk. Offline mode
# stops the Hugging Face client from checking for updates or downloading anything during a call.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from fastapi import FastAPI, Form, Request, UploadFile, WebSocket  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from loguru import logger  # noqa: E402
from pipecat.serializers.exotel import ExotelFrameSerializer  # noqa: E402
from pipecat.transports.base_transport import TransportParams  # noqa: E402
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection  # noqa: E402
from pipecat.transports.smallwebrtc.request_handler import (  # noqa: E402
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport  # noqa: E402
from pipecat.transports.websocket.fastapi import (  # noqa: E402
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from agent import config as call_config  # noqa: E402
from agent.bot import run_bot  # noqa: E402
from agent.events import fetch_call  # noqa: E402
from agent.stt_whisper import status as whisper_status  # noqa: E402
from agent.voice_local import (  # noqa: E402
    CONVERTER_URL,
    LOCAL_VOICE_WAV,
    converter_status,
    local_voice_name,
    set_local_voice,
)
from agent.voice_local import preview_wav as local_preview_wav  # noqa: E402

PHONE_RATE = 8000
BROWSER_RATE = 16000

app = FastAPI(title="visit-bot")


def _preload_whisper() -> None:
    """Load Whisper at startup so the first call doesn't wait for it."""
    try:
        from agent.stt_whisper import warm_up

        warm_up()
    except Exception:  # noqa: BLE001
        logger.exception("Whisper preload failed (`uv sync --extra whisper`)")


if whisper_status()["ready"]:
    import threading

    threading.Thread(target=_preload_whisper, daemon=True).start()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
webrtc = SmallWebRTCRequestHandler()
FALLBACK_BOOKING = {"id": None, "customerName": "", "date": None, "window": None}


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/flow")
async def flow_outline():
    """The call flow for the dashboard: sections of steps with plain-English titles."""
    mode = os.getenv("CALL_FLOW", "service-scheduling")
    if mode == "checklist":
        return {"title": "Checklist call", "version": None, "sections": [], "states": {}}
    from agent.flow import FlowSpec

    spec = FlowSpec.load(mode)
    sections = [{"title": v["title"], "about": v.get("about", ""), "states": v["states"]}
                for v in (spec.flow.get("sections") or {}).values()]
    intents = [{"name": n, "label": (i or {}).get("label") or n, "scope": scope}
               for scope, group in (("any time", spec.global_intents), ("one step", spec.local_intents))
               for n, i in group.items()]
    return {"title": spec.flow.get("title"), "version": str(spec.flow.get("version")), "sections": sections,
            "intents": intents,
            "states": {n: {"title": st.get("title") or n, "type": st.get("type")} for n, st in spec.states.items()}}


# ------------------------------------------------------------------ Call recordings (both sides, stereo WAV)
@app.get("/api/recordings/{call_id}.wav")
async def call_recording(call_id: str):
    from agent.recording import call_wav

    path = call_wav(call_id)
    if not (path and path.exists()):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return FileResponse(path, media_type="audio/wav", filename=f"{call_id}.wav")


# ------------------------------------------------------------------ Voice + call settings page
VOICE_PAGE = Path(__file__).resolve().parent / "static" / "voice.html"


@app.get("/voice")
async def voice_page():
    return FileResponse(VOICE_PAGE)


# ------------------------------------------------------------------ Local open-source voice (/voice page)
# "Piper + my voice": the sample stays on this machine (bot/local_voice.wav); bot/clone_tts converts.
async def _local_voice_payload() -> dict:
    return {"has_sample": LOCAL_VOICE_WAV.exists(), "name": local_voice_name(),
            "converter": await converter_status(), "converter_url": CONVERTER_URL}


@app.get("/api/local-voice")
async def get_local_voice():
    return await _local_voice_payload()


@app.post("/api/local-voice")
async def save_local_voice(file: UploadFile, name: str = Form(...), consent: bool = Form(False)):
    if not consent:
        return JSONResponse({"error": "Consent is required: only clone your own voice."}, status_code=400)
    audio = await file.read()
    if len(audio) < 16000:
        return JSONResponse({"error": "That clip is too short."}, status_code=400)
    set_local_voice(audio, name.strip()[:100])
    logger.info(f"local voice sample saved ({len(audio)} bytes, {name})")
    return await _local_voice_payload()


@app.delete("/api/local-voice")
async def delete_local_voice():
    set_local_voice(None)
    return await _local_voice_payload()


@app.post("/api/local-voice/preview")
async def preview_local_voice(request: Request):
    body = await request.json()
    cfg = call_config.load()
    try:
        wav = await local_preview_wav(body["text"][:1000], cfg["piper_voice"], cfg["clone_tau"],
                                      cfg.get("piper_voice_en"))
    except Exception as e:  # noqa: BLE001 - converter down, no sample yet
        return JSONResponse({"error": str(e) or repr(e)}, status_code=502)
    return Response(wav, media_type="audio/wav")


# ------------------------------------------------------------------ Call settings (/voice page)
async def _config_payload() -> dict:
    values, sources = call_config.resolve()
    return {"fields": call_config.public_schema(), "values": values, "sources": sources,
            "whisper_status": whisper_status()}


@app.get("/api/config")
async def get_config():
    return await _config_payload()


@app.put("/api/config")
async def put_config(request: Request):
    body = await request.json()
    try:
        call_config.save(body.get("values") or {})
    except call_config.ConfigError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    logger.info(f"call config updated: {body.get('values')}")
    return await _config_payload()


@app.delete("/api/config")
async def reset_config(request: Request):
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    call_config.reset(body.get("keys"))
    return await _config_payload()


async def _booking_for(call_id: str | None, call_sid: str | None) -> tuple[str, dict]:
    call = await fetch_call(call_id=call_id) or await fetch_call(call_sid=call_sid)
    if call:
        return call["id"], call.get("booking") or FALLBACK_BOOKING
    logger.warning(f"no app call found (call_id={call_id}, sid={call_sid}); using fallback booking")
    return call_id or f"LOCAL-{uuid.uuid4().hex[:6]}", FALLBACK_BOOKING


# ------------------------------------------------------------------ Exotel (phone)
@app.websocket("/exotel")
async def exotel_ws(ws: WebSocket):
    await ws.accept()
    # Exotel sends `connected`, then `start` (with stream_sid, call_sid, custom_parameters).
    start: dict = {}
    for _ in range(5):
        msg = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        if msg.get("event") == "start":
            start = msg
            break
    if not start:
        logger.error("Exotel websocket closed before start event")
        await ws.close()
        return
    s = start.get("start", {})
    stream_sid = start.get("stream_sid") or s.get("stream_sid")
    call_sid = s.get("call_sid")
    custom = s.get("custom_parameters") or {}
    call_id_hint = custom.get("call_id") or ws.query_params.get("call_id")
    call_id, booking = await _booking_for(call_id_hint, call_sid)
    logger.info(f"Exotel start stream={stream_sid} call_sid={call_sid} call_id={call_id}")

    transport = FastAPIWebsocketTransport(
        websocket=ws,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=ExotelFrameSerializer(stream_sid=stream_sid, call_sid=call_sid),
        ),
    )
    # 8 kHz phone leg; local Whisper resamples each segment to 16 kHz itself.
    await run_bot(transport, call_id=call_id, booking=booking, telephony=True,
                  sample_rate=PHONE_RATE, call_sid=call_sid)


# ------------------------------------------------------------------ Browser (WebRTC)
@app.post("/api/offer")
async def offer(request: Request):
    body = await request.json()
    req = SmallWebRTCRequest.from_dict(body)
    data = req.request_data or {}

    async def on_connection(conn: SmallWebRTCConnection):
        call_id, booking = await _booking_for(data.get("call_id"), None)
        transport = SmallWebRTCTransport(
            webrtc_connection=conn,
            params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        )
        asyncio.create_task(run_bot(transport, call_id=call_id, booking=booking, telephony=False,
                                    sample_rate=BROWSER_RATE))

    return await webrtc.handle_web_request(req, on_connection)
