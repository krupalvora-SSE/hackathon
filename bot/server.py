"""Bot server: SmallWebRTC offer (browser calls) + /voice settings page.

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
from fastapi import FastAPI, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from loguru import logger  # noqa: E402
from pipecat.transports.base_transport import TransportParams  # noqa: E402
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection  # noqa: E402
from pipecat.transports.smallwebrtc.request_handler import (  # noqa: E402
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport  # noqa: E402

from agent import config as call_config  # noqa: E402
from agent.bot import run_bot  # noqa: E402
from agent.events import fetch_call  # noqa: E402
from agent.stt_indic import status as indic_status  # noqa: E402
from agent.stt_whisper import status as whisper_status  # noqa: E402
from agent.voice import active_voice_id, fix_spoken_text, set_active_voice  # noqa: E402
from agent.voice_local import (  # noqa: E402
    CONVERTER_URL,
    LOCAL_VOICE_WAV,
    converter_status,
    local_voice_name,
    set_local_voice,
)
from agent.voice_local import preview_wav as local_preview_wav  # noqa: E402

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


def _preload_whisper() -> None:
    """Load Whisper at startup when it is the selected STT engine, or IndicConformer's fallback."""
    try:
        from agent.stt_whisper import warm_up

        warm_up()
    except Exception:  # noqa: BLE001
        logger.exception("Whisper preload failed (`uv sync --extra indic --extra whisper`)")


_stt = call_config.load()["stt_provider"]
if (_stt == "whisper" or (_stt == "indic" and not indic_status()["ready"])) and whisper_status()["ready"]:
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
    return {"fields": call_config.public_schema(), "values": values, "sources": sources, "indic_status": indic_status(),
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
