"""Bot server: Exotel Voicebot websocket (phone) + SmallWebRTC offer (browser fallback).

Run: uv run uvicorn server:app --port 7860
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, WebSocket  # noqa: E402
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

from agent.bot import run_bot  # noqa: E402
from agent.events import fetch_call  # noqa: E402

PHONE_RATE = 8000
BROWSER_RATE = 16000

app = FastAPI(title="visit-bot")
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
