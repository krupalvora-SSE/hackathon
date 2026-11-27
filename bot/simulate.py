"""Text-mode call simulator: same prompt, tools and guard as the voice bot, no audio.

  uv run python simulate.py                 # you type as the customer
  uv run python simulate.py --auto "busy, asks for Sunday first, then agrees to Monday morning"

run_call() is also what eval/conv_bench.py batches over personas and LLM settings.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent.engine import make_engine

load_dotenv()
GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
BOOKINGS = json.loads((Path(__file__).resolve().parent.parent / "app" / "data" / "bookings.json").read_text())


def llm_client(provider: str | None = None, model: str | None = None) -> tuple[OpenAI, str]:
    provider = provider or os.getenv("LLM_PROVIDER", "google")
    if provider == "google":  # Gemini's OpenAI-compatible endpoint
        return (OpenAI(api_key=os.environ["GOOGLE_API_KEY"], base_url=GEMINI_OPENAI_URL),
                model or os.getenv("LLM_MODEL") or "gemini-3.6-flash")
    return (OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.getenv("LLM_BASE_URL") or None),
            model or os.getenv("LLM_MODEL") or "gpt-4.1-mini")


def run_call(engine, client: OpenAI, model: str, *, persona: str | None = None, ask=None,
             customer: tuple[OpenAI, str] | None = None, max_turns: int = 16, temperature: float = 0.3,
             max_tokens: int | None = None, log=print) -> dict:
    """One text call. The customer is an LLM playing `persona`, or `ask(agent_line)` (a human).
    Returns {record, transcript, llm_ms (per agent LLM call), tokens_in, tokens_out}."""
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
              "parameters": {"type": "object", "properties": t["properties"], "required": t["required"]}}}
             for t in engine.tools]
    msgs = [{"role": "system", "content": engine.system_prompt()},
            {"role": "assistant", "content": " ".join(engine.opening())}]
    cust_client, cust_model = customer or (client, model)
    customer_msgs = [{"role": "system", "content":
                      f"You are a Indian customer on a phone call, replying in short Hinglish (1 sentence). "
                      f"Persona: {persona}. Never break character."}]
    transcript = [("agent", msgs[-1]["content"])]
    llm_ms: list[int] = []
    tok_in = tok_out = 0
    log(f"AGENT: {msgs[-1]['content']}")

    for _ in range(max_turns):
        heard = next((t for r, t in reversed(transcript) if r == "agent"), "")  # last thing the bot said
        if persona:
            customer_msgs.append({"role": "user", "content": heard or ""})
            said = cust_client.chat.completions.create(model=cust_model, messages=customer_msgs).choices[0].message.content
            customer_msgs.append({"role": "assistant", "content": said})
            log(f"CUSTOMER: {said}")
        else:
            said = ask(heard)
        transcript.append(("customer", said))
        msgs.append({"role": "user", "content": said})
        engine.heard_customer()

        while True:
            t = time.monotonic()
            kw = {"max_tokens": max_tokens} if max_tokens else {}
            resp = client.chat.completions.create(model=model, messages=msgs, tools=tools,
                                                  temperature=temperature, **kw)
            llm_ms.append(int((time.monotonic() - t) * 1000))
            if resp.usage:
                tok_in += resp.usage.prompt_tokens or 0
                tok_out += resp.usage.completion_tokens or 0
            m = resp.choices[0].message
            msgs.append(m.model_dump(exclude_none=True))
            if not m.tool_calls:
                log(f"AGENT: {m.content}")
                transcript.append(("agent", m.content or ""))
                break
            run_llm, ask_next = True, None
            for tc in m.tool_calls:
                act = engine.dispatch(tc.function.name, json.loads(tc.function.arguments or "{}"))
                log(f"   ⚙ {tc.function.name}{tc.function.arguments} -> {act.result}")
                transcript.append(("tool", f"{tc.function.name}{tc.function.arguments}"))
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(act.result, ensure_ascii=False)})
                for line in act.speak:
                    log(f"AGENT (system): {line}")
                    transcript.append(("agent", line))
                    msgs.append({"role": "assistant", "content": line})
                run_llm = run_llm and act.run_llm
                ask_next = getattr(act, "ask_next", None) or (None if act.run_llm or act.speak else ask_next)
            if ask_next and not engine.outcome:
                log(f"AGENT (system): {ask_next}")
                transcript.append(("agent", ask_next))
                msgs.append({"role": "assistant", "content": ask_next})
            if engine.outcome or not run_llm:
                break
        if engine.outcome:
            break

    engine.hang_up()
    return {"record": engine.record(), "transcript": transcript, "llm_ms": llm_ms,
            "tokens_in": tok_in, "tokens_out": tok_out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", help="persona for an LLM-played customer")
    ap.add_argument("--max-turns", type=int, default=16)
    ap.add_argument("--booking", default="BK-1001", help="booking id from app/data/bookings.json")
    ap.add_argument("--flow", help="CALL_FLOW override: service-scheduling (default) or checklist")
    a = ap.parse_args()
    booking = next(b for b in BOOKINGS if b["id"] == a.booking)

    client, model = llm_client()
    engine = make_engine("SIM", booking, on_event=lambda e: print(f"   · {e}"), mode=a.flow)
    if engine.outcome:
        print(f"not dialled: {engine.outcome}")
        return
    out = run_call(engine, client, model, persona=a.auto, ask=lambda _: input("CUSTOMER> "),
                   max_turns=a.max_turns)
    print("\nRECORD:", json.dumps(out["record"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
