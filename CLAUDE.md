# AMC Cleaning Voice Bot — SolarSquare AI Voice Agent Hackathon

We are building an outbound/inbound **phone voice agent** that handles the **AMC (Annual Maintenance Contract) solar panel cleaning** journey for existing SolarSquare customers, in **Hindi (Hinglish)** first.

Hackathon tracks covered: **#4 Service slot scheduling** and **#5 Service slot confirmation** (plus reschedule / cancel / handover).

## Judging (build for these, in this order)
1. Getting the job done: correct outcome + correct structured record, every call
2. Speed: voice-to-voice reply < 1s (world-class ≈ 200–500ms)
3. Natural conversation: cloned voice of a team member, human-sounding Hinglish
4. Robustness: noise, interruptions, silence, "beta se baat karo", handover to human
5. Cost per call

## Hard rules (from organisers)
- Never call real customers; test numbers only.
- Agent discloses the call is recorded, stops when asked, never promises prices, savings or subsidy amounts.
- No API keys in code, Slack or WhatsApp. Use `.env` (git-ignored).
- Customer recordings stay inside the company.

## Skills in this repo (`.claude/skills/`)
| Skill | Use when |
|---|---|
| `amc-voice-architecture` | Picking or wiring the stack: telephony, STT, LLM, TTS, VAD, latency |
| `amc-call-flows` | Designing or changing the conversation state machine and outcomes |
| `hindi-voice-scripting` | Writing prompts and agent lines, Hinglish, numbers, dates, TTS text |
| `slot-booking-tools` | Building the availability, booking and reschedule APIs and LLM tools |
| `call-robustness-handoff` | Silence, barge-in, noise, wrong person, DNC, human transfer, compliance |
| `voice-agent-eval` | Testing, simulated callers, latency measurement, demo prep |

## Call flows as config (`flows/`)
Each folder in `flows/` is one call flow defined as YAML: states, intents, Beat Plan/Zeus actions, policies and Hindi/English prompts. `flows/service-scheduling/` (TECH-56) is the Beat-Plan-driven scheduling call: confirm slot, 4-hour band, alternate days, readiness check, site on hold, recall, RM callback.
- Change behaviour by editing the YAML, not bot code. Add a scenario in `scenarios.yaml` for every new branch.
- After any change run `.venv/bin/python tools/flowcheck.py --write` (first time: `python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt`). It must print `OK`; it also redraws the diagram in the flow's README.
- Bump `version` in `flow.yaml` and add a `CHANGELOG.md` line.
