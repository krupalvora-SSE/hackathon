"""Bot server: Exotel Voicebot websocket (phone) + SmallWebRTC offer (browser fallback).

Run: uv run uvicorn server:app --port 7860
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import httpx  # noqa: E402
from fastapi import FastAPI, Form, Request, UploadFile, WebSocket  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from loguru import logger  # noqa: E402
try:  # pyrnnoise has no wheel for some platforms (e.g. macOS < 26); run without it there
    from pipecat.audio.filters import rnnoise_filter as _rnnoise  # noqa: E402

    if _rnnoise.RNNoise is None:  # pipecat swallows the missing pyrnnoise and imports anyway
        raise ImportError("pyrnnoise is not installed")
    RNNoiseFilter = _rnnoise.RNNoiseFilter
except Exception as e:  # noqa: BLE001
    RNNoiseFilter = None
    logger.warning(f"RNNoise unavailable ({e}); phone audio runs without noise suppression")
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
from agent.stt_indic import status as indic_status  # noqa: E402
from agent.voice import active_voice_id, fix_spoken_text, set_active_voice  # noqa: E402

PHONE_RATE = 8000
BROWSER_RATE = 16000

app = FastAPI(title="visit-bot")


def _preload_indic() -> None:
    """Load IndicConformer in the background at startup when it is the selected STT engine."""
    try:
        from agent.stt_indic import load_model

        load_model()
    except Exception:  # noqa: BLE001
        logger.exception("IndicConformer preload failed (check HF_TOKEN and `uv sync --extra indic`)")


if call_config.load()["stt_provider"] == "indic" and indic_status()["ready"]:
    import threading

    threading.Thread(target=_preload_indic, daemon=True).start()
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
    return {"title": spec.flow.get("title"), "version": str(spec.flow.get("version")), "sections": sections,
            "states": {n: {"title": st.get("title") or n, "type": st.get("type")} for n, st in spec.states.items()}}


# ------------------------------------------------------------------ Voice cloning (/voice page)
SARVAM = "https://api.sarvam.ai"
VOICE_PAGE = Path(__file__).resolve().parent / "static" / "voice.html"


def _sarvam_headers() -> dict:
    return {"api-subscription-key": os.environ["SARVAM_API_KEY"]}


def _sarvam_error(r: httpx.Response) -> JSONResponse:
    try:
        detail = r.json()
    except ValueError:
        detail = r.text[:500]
    logger.warning(f"Sarvam {r.request.method} {r.request.url.path} -> {r.status_code}: {detail}")
    return JSONResponse({"error": detail, "status": r.status_code}, status_code=r.status_code)


@app.get("/voice")
async def voice_page():
    return FileResponse(VOICE_PAGE)


@app.get("/api/voices")
async def list_voices():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{SARVAM}/voices", headers=_sarvam_headers(), params={"limit": 200})
    if r.status_code != 200:
        return _sarvam_error(r)
    voices = [v for v in r.json()["data"]["voices"] if v.get("status") == "active"]
    return {"voices": voices, "active": active_voice_id(),
            "pinned_by_env": bool(os.getenv("SARVAM_CLONE_VOICE_ID"))}


@app.post("/api/voices")
async def create_voice(file: UploadFile, name: str = Form(...), consent: bool = Form(False),
                       language: str = Form("hi-IN"), make_active: bool = Form(True)):
    if not consent:
        return JSONResponse({"error": "Consent is required: only clone your own voice."}, status_code=400)
    audio = await file.read()
    fname = file.filename or "voice.wav"
    ctype = file.content_type or ""
    if not ctype.startswith("audio/"):  # Sarvam rejects application/octet-stream
        ctype = mimetypes.guess_type(fname)[0] or "audio/wav"
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{SARVAM}/voices/create", headers=_sarvam_headers(),
                         files={"file": (fname, audio, ctype)},
                         data={"name": name.strip()[:100], "language": language, "style": "conversational",
                               "remove_background_noise": "true"})
    if r.status_code not in (200, 201):
        return _sarvam_error(r)
    data = r.json()["data"]
    if make_active:
        set_active_voice(data["voice_id"], name)
    logger.info(f"cloned voice created {data['voice_id']} ({name}), active={make_active}")
    return {**data, "active": active_voice_id()}


@app.post("/api/voices/active")
async def choose_voice(request: Request):
    body = await request.json()
    set_active_voice(body.get("voice_id"), body.get("name"))
    return {"active": active_voice_id()}


@app.post("/api/voices/preview")
async def preview_voice(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{SARVAM}/voices/clone", headers=_sarvam_headers(),
                         data={"voice_id": body["voice_id"], "text": fix_spoken_text(body["text"][:1000]),
                               "language_code": "hi-IN", "output_audio_codec": "wav"})
    if r.status_code != 200:
        return _sarvam_error(r)
    return Response(base64.b64decode(r.json()["audio_b64"]), media_type="audio/wav")


@app.delete("/api/voices/{voice_id}")
async def delete_voice(voice_id: str):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(f"{SARVAM}/voices/delete/{voice_id}", headers=_sarvam_headers())
    if r.status_code not in (200, 204):
        return _sarvam_error(r)
    if active_voice_id() == voice_id:
        set_active_voice(None)
    return {"deleted": voice_id, "active": active_voice_id()}


# ------------------------------------------------------------------ Call settings (/voice page)
def _config_payload() -> dict:
    values, sources = call_config.resolve()
    return {"fields": call_config.public_schema(), "values": values, "sources": sources,
            "rnnoise_available": RNNoiseFilter is not None, "indic_status": indic_status()}


@app.get("/api/config")
async def get_config():
    return _config_payload()


@app.put("/api/config")
async def put_config(request: Request):
    body = await request.json()
    try:
        call_config.save(body.get("values") or {})
    except call_config.ConfigError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    logger.info(f"call config updated: {body.get('values')}")
    return _config_payload()


@app.delete("/api/config")
async def reset_config(request: Request):
    body = await request.json() if int(request.headers.get("content-length") or 0) else {}
    call_config.reset(body.get("keys"))
    return _config_payload()


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
            # phone line has no browser-side suppression; toggle on the /voice page
            audio_in_filter=RNNoiseFilter() if RNNoiseFilter and call_config.load()["rnnoise"] else None,
            add_wav_header=False,
            serializer=ExotelFrameSerializer(stream_sid=stream_sid, call_sid=call_sid),
        ),
    )
    await run_bot(transport, call_id=call_id, booking=booking, telephony=True,
                  sample_rate=PHONE_RATE, call_sid=call_sid)


# ------------------------------------------------------------------ Browser (WebRTC fallback)
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
