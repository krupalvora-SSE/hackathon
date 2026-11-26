"""Text-mode call simulator: same prompt, tools and guard as the voice bot, no audio.

  uv run python simulate.py                 # you type as the customer
  uv run python simulate.py --auto "busy, asks for Sunday first, then agrees to Monday morning"
"""
from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

from agent.prompts import greeting, system_prompt
from agent.slots import CallState
from agent.tools import TOOLS, dispatch

load_dotenv()
BOOKING = {"id": "BK-1001", "customerName": "Rahul Joshi", "sseId": "SSE-PUN-20431",
           "date": "2026-11-28", "window": "09-12"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", help="persona for an LLM-played customer")
    ap.add_argument("--max-turns", type=int, default=16)
    a = ap.parse_args()

    client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.getenv("LLM_BASE_URL") or None)
    model = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    state = CallState(call_id="SIM", booking=BOOKING, on_event=lambda e: print(f"   · {e}"))
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
              "parameters": {"type": "object", "properties": t["properties"], "required": t["required"]}}}
             for t in TOOLS]
    msgs = [{"role": "system", "content": system_prompt(BOOKING, state.today)},
            {"role": "assistant", "content": greeting(BOOKING)}]
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
            run_llm = True
            for tc in m.tool_calls:
                act = dispatch(state, tc.function.name, json.loads(tc.function.arguments or "{}"))
                print(f"   ⚙ {tc.function.name}{tc.function.arguments} -> {act.result}")
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(act.result, ensure_ascii=False)})
                for line in act.speak:
                    print(f"AGENT (system): {line}")
                    msgs.append({"role": "assistant", "content": line})
                run_llm = run_llm and act.run_llm
            if state.outcome or not run_llm:
                break
        if state.outcome:
            break

    print("\nRECORD:", json.dumps(state.record(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
