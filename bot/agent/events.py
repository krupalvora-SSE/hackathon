"""Fire-and-forget events to the Node app. A slow or dead dashboard never blocks a call."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from loguru import logger

APP_URL = os.getenv("APP_URL", "http://localhost:3000").rstrip("/")
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


class Emitter:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._client = httpx.AsyncClient(timeout=2.0)
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def emit(self, event: dict) -> None:
        if self._task is None:
            self._task = asyncio.get_event_loop().create_task(self._run())
        self._queue.put_nowait(event)

    async def _run(self) -> None:
        url = f"{APP_URL}/api/calls/{self.call_id}/events"
        while (event := await self._queue.get()) is not None:  # sequential keeps order
            try:
                await self._client.post(url, json=event)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"event post failed ({event.get('type')}): {e}")

    async def close(self) -> None:
        if self._task:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._task, 5)
            except asyncio.TimeoutError:
                self._task.cancel()
        await self._client.aclose()


def save_result(record: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{record['call_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return path


async def fetch_call(call_id: str | None = None, call_sid: str | None = None) -> dict | None:
    """Look up the call (and its booking) the Node app created."""
    if not (call_id or call_sid):
        return None
    path = f"/api/calls/{call_id}" if call_id else f"/api/calls/by-sid/{call_sid}"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{APP_URL}{path}")
            return r.json() if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"call lookup failed: {e}")
        return None
