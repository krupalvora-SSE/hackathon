"""What an admin taught the agent on the dashboard's Questions view: approved answers and extra intent phrases."""
from __future__ import annotations

import os

import httpx
from loguru import logger


def clean(data: object) -> dict:
    """Only well-formed entries, capped, so a bad row can never break a call."""
    if not isinstance(data, dict):
        return {}
    faqs = [{"id": str(f["id"]), "question": str(f["question"])[:200], "answer": str(f["answer"])[:400]}
            for f in data.get("faqs", []) if isinstance(f, dict) and f.get("id") and f.get("question") and f.get("answer")]
    intents = {str(k): [str(p)[:200] for p in v][:20] for k, v in (data.get("intents") or {}).items()
               if isinstance(v, list)}
    return {"faqs": faqs[:40], "intents": intents}


async def fetch_knowledge() -> dict:
    """Read at the start of every call, so an answer saved now is used by the next call. {} if the app is down."""
    url = os.getenv("APP_URL", "http://localhost:3000").rstrip("/") + "/api/knowledge"
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(url)
            r.raise_for_status()
            return clean(r.json())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"could not read the taught answers, calling without them: {e}")
        return {}
