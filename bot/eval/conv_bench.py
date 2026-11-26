"""Conversation bench: LLM settings x simulated customers, scored on the call record.

The customer is always played by the same fixed model (CUSTOMER_MODEL) so a difference between two
configs comes from the bot's settings, not from a different caller.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent
PERSONAS = ROOT / "personas.yaml"
CUSTOMER_MODEL = os.getenv("EVAL_CUSTOMER_MODEL", "gemini-3.6-flash")


def load_personas(only: list[str] | None = None) -> tuple[list[dict], list[str]]:
    data = yaml.safe_load(PERSONAS.read_text())
    must_not = data.get("must_not_say_default", [])
    ps = data["personas"]
    if only:
        ps = [p for p in ps if p["id"] in only]
    return ps, must_not


def _one(p: dict, cfg: dict, must_not: list[str]) -> dict:
    from agent.engine import make_engine
    from simulate import BOOKINGS, llm_client, run_call

    booking = next(b for b in BOOKINGS if b["id"] == p["booking"])
    client, model = llm_client(cfg["llm_provider"], cfg["llm_model"])
    customer = llm_client("google", CUSTOMER_MODEL)
    engine = make_engine("EVAL", booking)
    try:
        out = run_call(engine, client, model, persona=p["persona"], customer=customer,
                       temperature=cfg["llm_temperature"], max_tokens=cfg["llm_max_tokens"], log=lambda *_: None)
    except Exception as e:  # noqa: BLE001
        return {"persona": p["id"], "pass": False, "failure": "error", "detail": f"{type(e).__name__}: {e}"[:300]}
    rec = out["record"]
    agent_lines = [t for r, t in out["transcript"] if r == "agent"]
    bad = [rx for rx in p.get("must_not_say", must_not) if any(re.search(rx, ln or "") for ln in agent_lines)]
    empty = sum(1 for ln in agent_lines if not (ln or "").strip())
    outcome = rec.get("outcome")
    ok_outcome = outcome in p["expect"]
    failure = None
    if bad:
        failure = "compliance"
    elif not ok_outcome:
        failure = "no_outcome" if outcome in (None, "hung_up") else "wrong_outcome"
    elif empty:
        failure = "empty_reply"
    return {"persona": p["id"], "pass": failure is None, "failure": failure, "outcome": outcome,
            "expected": p["expect"], "compliance_hits": bad, "turns": sum(1 for r, _ in out["transcript"] if r == "customer"),
            "llm_ms": out["llm_ms"], "tokens_in": out["tokens_in"], "tokens_out": out["tokens_out"],
            "transcript": out["transcript"]}


async def run(cfg: dict, personas: list[dict], must_not: list[str], repeats: int = 2,
              concurrency: int = 4) -> tuple[dict, list[dict]]:
    sem = asyncio.Semaphore(concurrency)

    async def go(p):
        async with sem:
            return await asyncio.to_thread(_one, p, cfg, must_not)

    rows = list(await asyncio.gather(*(go(p) for p in personas for _ in range(repeats))))
    ms = sorted(m for r in rows for m in r.get("llm_ms", []))
    per = {}
    for p in personas:
        rs = [r for r in rows if r["persona"] == p["id"]]
        per[p["id"]] = round(sum(r["pass"] for r in rs) / len(rs), 2)
    fails: dict[str, int] = {}
    for r in rows:
        if r["failure"]:
            fails[r["failure"]] = fails.get(r["failure"], 0) + 1
    from agent.bot import RATE

    tin = float(np.mean([r.get("tokens_in", 0) for r in rows]))
    tout = float(np.mean([r.get("tokens_out", 0) for r in rows]))
    return {
        "calls": len(rows),
        "pass_rate": round(sum(r["pass"] for r in rows) / len(rows), 3),
        "compliance_fails": fails.get("compliance", 0),
        "llm_p50_ms": int(np.percentile(ms, 50)) if ms else None,
        "llm_p90_ms": int(np.percentile(ms, 90)) if ms else None,
        "avg_turns": round(float(np.mean([r.get("turns", 0) for r in rows])), 1),
        "llm_cost_inr": round(tin / 1e6 * RATE["llm_per_m_in"] + tout / 1e6 * RATE["llm_per_m_out"], 3),
        "per_persona": per, "failures": fails,
    }, rows
