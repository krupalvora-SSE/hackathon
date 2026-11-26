# visit-bot (Python, Pipecat 1.17)

Voice agent that confirms a solar panel cleaning visit and collects 4 keys:
`visit_date`, `visit_time_slot`, `ladder_available`, `water_source_available`.
The Node app in `../app` owns bookings, calls and the dashboard. This bot owns the call audio.

```
Exotel Voicebot ──wss /exotel──► bot (8 kHz) ──POST events──► app :3000 ──SSE──► dashboard
Browser mic ─────POST /api/offer (WebRTC, 16 kHz)──┘         ▲ GET /api/calls/:id (booking)
```

## Run
```bash
cp .env.example .env            # SARVAM_API_KEY, GOOGLE_API_KEY (Gemini)
uv sync
uv run uvicorn server:app --port 7860
cd ../app && npm start          # http://localhost:3000 → "Browser" button = mic test
uv run pytest -q                # engine tests
uv run python simulate.py       # text-mode call (or --auto "persona")
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
| `agent/slots.py` | Slot config + `CallState` guard. A call can't end `confirmed` until all 4 keys are saved, read back and confirmed. Any change re-triggers the read-back. |
| `agent/normalize.py` | Hinglish, Devanagari and Marathi parsers for dates, time slots and yes/no |
| `agent/tools.py` | LLM tool schemas + `dispatch()`. The system (not the LLM) speaks the read-back and goodbyes |
| `agent/prompts.py` | System prompt, greeting with recording disclosure, fixed goodbyes |
| `agent/bot.py` | Pipecat pipeline, silence handling (6s reprompt, 3 silences means `no_answer`), latency and cost metrics |
| `agent/events.py` | Fire-and-forget events to the app, plus `results/<call_id>.json` backup |
| `server.py` | `/exotel` websocket, `/api/offer` WebRTC, `/health` |

Outcomes: `confirmed`, `callback`, `do_not_call`, `wrong_number` (agent); `no_answer`, `handed_to_human`, `hung_up` (system).
