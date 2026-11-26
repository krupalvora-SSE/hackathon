# visit-bot (Python, Pipecat 1.17)

Voice agent for solar maintenance visits. Two call engines, picked with `CALL_FLOW` in `.env`:

- **`service-scheduling` (default):** runs the YAML flow in `../flows/service-scheduling/` (SSE-15527). Offers the Beat Plan primary slot, 4-hour bands, backup days, checks ladder / water / light, puts sites on hold with consent, recall, RM callback, and posts a Beat Plan comment after every call. The LLM only classifies what the customer said (`customer_said(intent, …)`); `agent/flow.py` picks the next step and the exact line to speak. Change behaviour by editing the YAML, not Python.
- **`checklist`:** the original engine. Collects 4 keys
`visit_date`, `visit_time_slot`, `ladder_available`, `water_source_available`.
The Node app in `../app` owns bookings, calls and the dashboard. This bot owns the call audio.

```
Exotel Voicebot ──wss /exotel──► bot (8 kHz) ──POST events──► app :3000 ──SSE──► dashboard
Browser mic ─────POST /api/offer (WebRTC, 16 kHz)──┘         ▲ GET /api/calls/:id (booking)
```

## Run
```bash
cp .env.example .env            # SARVAM_API_KEY, LLM_API_KEY (+ LLM_BASE_URL/LLM_MODEL)
uv sync
uv run uvicorn server:app --port 7860
cd ../app && npm start          # http://localhost:3000 → "Browser" button = mic test
uv run pytest -q                # engine tests
uv run python simulate.py       # text-mode call (or --auto "persona", --booking BK-1003, --flow checklist)
```

## Exotel setup (phone mode)
1. Expose the bot over TLS: `ngrok http 7860`, giving `wss://<id>.ngrok.app/exotel`.
2. Build the flow: **Voicebot** (URL above) → **Passthru** (`https://<app-host>/exotel/passthru`)
   → on 200 go to **Connect** (human agent number), otherwise **Hangup**.
3. Fill `app/.env`: `EXOTEL_API_KEY/TOKEN/ACCOUNT_SID/CALLER_ID/FLOW_ID`.
4. Only call team or judge test numbers.

## Where things live
| File | Role |
|---|---|
| `agent/engine.py` | Picks the engine from `CALL_FLOW`; `bot.py` and `simulate.py` only talk to this |
| `agent/flow.py` | Runs a YAML flow: transitions, readiness checklist, alternate-day offers, Hindi/English lines, call record |
| `agent/beatplan.py` | Beat Plan / Zeus actions. `MockBeatPlan` reads `beatPlan` from `app/data/bookings.json` and reports every write as a `beatplan` event |
| `agent/slots.py` | Slot config + `CallState` guard. A call can't end `confirmed` until all 4 keys are saved, read back and confirmed. Any change re-triggers the read-back. |
| `agent/normalize.py` | Hinglish, Devanagari and Marathi parsers for dates, time slots and yes/no |
| `agent/tools.py` | LLM tool schemas + `dispatch()`. The system (not the LLM) speaks the read-back and goodbyes |
| `agent/prompts.py` | System prompt, greeting with recording disclosure, fixed goodbyes |
| `agent/bot.py` | Pipecat pipeline, silence handling (6s reprompt, 3 silences means `no_answer`), latency and cost metrics |
| `agent/events.py` | Fire-and-forget events to the app, plus `results/<call_id>.json` backup |
| `server.py` | `/exotel` websocket, `/api/offer` WebRTC, `/health` |

Outcomes, flow mode: see `flows/service-scheduling/README.md` (`slot_confirmed`, `site_on_hold`, `rm_callback`, …, plus `hung_up`).
Outcomes, checklist mode: `confirmed`, `callback`, `do_not_call`, `wrong_number` (agent); `no_answer`, `handed_to_human`, `hung_up` (system).
