"""Text-mode call simulator: same prompt, tools and guard as the voice bot, no audio.

  uv run python simulate.py                 # you type as the customer
  uv run python simulate.py --auto "busy, asks for Sunday first, then agrees to Monday morning"
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from agent.engine import make_engine

load_dotenv()
GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
BOOKINGS = json.loads((Path(__file__).resolve().parent.parent / "app" / "data" / "bookings.json").read_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", help="persona for an LLM-played customer")
    ap.add_argument("--max-turns", type=int, default=16)
    ap.add_argument("--booking", default="BK-1001", help="booking id from app/data/bookings.json")
    ap.add_argument("--flow", help="CALL_FLOW override: service-scheduling (default) or checklist")
    a = ap.parse_args()
    booking = next(b for b in BOOKINGS if b["id"] == a.booking)

    if os.getenv("LLM_PROVIDER", "google") == "google":  # Gemini's OpenAI-compatible endpoint
        client = OpenAI(api_key=os.environ["GOOGLE_API_KEY"], base_url=GEMINI_OPENAI_URL)
        model = os.getenv("LLM_MODEL", "gemini-3.6-flash")
    else:
        client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.getenv("LLM_BASE_URL") or None)
        model = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    engine = make_engine("SIM", booking, on_event=lambda e: print(f"   · {e}"), mode=a.flow)
    if engine.outcome:
        print(f"not dialled: {engine.outcome}")
        return
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
              "parameters": {"type": "object", "properties": t["properties"], "required": t["required"]}}}
             for t in engine.tools]
    msgs = [{"role": "system", "content": engine.system_prompt()},
            {"role": "assistant", "content": " ".join(engine.opening())}]
    customer_msgs = [{"role": "system", "content":
                      f"You are a Indian customer on a phone call, replying in short Hinglish (1 sentence). "
                      f"Persona: {a.auto}. Never break character."}]
    print(f"AGENT: {msgs[-1]['content']}")

    for _ in range(a.max_turns):
        if a.auto:
            customer_msgs.append({"role": "user", "content": msgs[-1]["content"] or ""})
            said = client.chat.completions.create(model=model, messages=customer_msgs).choices[0].message.content
            customer_msgs.append({"role": "assistant", "content": said})
            print(f"CUSTOMER: {said}")
        else:
            said = input("CUSTOMER> ")
        msgs.append({"role": "user", "content": said})

        while True:
            m = client.chat.completions.create(model=model, messages=msgs, tools=tools, temperature=0.3).choices[0].message
            msgs.append(m.model_dump(exclude_none=True))
            if not m.tool_calls:
                print(f"AGENT: {m.content}")
                break
            run_llm, ask_next = True, None
            for tc in m.tool_calls:
                act = engine.dispatch(tc.function.name, json.loads(tc.function.arguments or "{}"))
                print(f"   ⚙ {tc.function.name}{tc.function.arguments} -> {act.result}")
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(act.result, ensure_ascii=False)})
                for line in act.speak:
                    print(f"AGENT (system): {line}")
                    msgs.append({"role": "assistant", "content": line})
                run_llm = run_llm and act.run_llm
                ask_next = getattr(act, "ask_next", None) or (None if act.run_llm or act.speak else ask_next)
            if ask_next and not engine.outcome:
                print(f"AGENT (system): {ask_next}")
                msgs.append({"role": "assistant", "content": ask_next})
            if engine.outcome or not run_llm:
                break
        if engine.outcome:
            break

    engine.hang_up()
    print("\nRECORD:", json.dumps(engine.record(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
