# visit-bot (Python, Pipecat 1.17)

Voice agent for solar maitenance visits. Two call engines, picked with `CALL_FLOW` in `.env`:

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
cp .env.example .env            # SARVAM_API_KEY, GOOGLE_API_KEY (Gemini)
uv sync
uv run uvicorn server:app --port 7860
cd ../app && npm start          # http://localhost:3000 → "Browser" button = mic test
uv run pytest -q                # engine tests
uv run python simulate.py       # text-mode call (or --auto "persona", --booking BK-1003, --flow checklist)
```

## Cloned voice
Open http://localhost:7860/voice (bot server running). Read the script for 12–30 s, tick consent,
**Create my voice**, preview it, then **Use for bot**. The next call speaks in that voice (Sarvam
Instant Voice Cloning, streamed over `wss://api.sarvam.ai/voices/clone/ws`, see `agent/voice.py`).
The choice lives in `bot/voice.json` (git-ignored); `SARVAM_CLONE_VOICE_ID` in `.env` overrides it.
Clones copy the sample's pace, so set `SARVAM_TTS_PACE=1.2` if the voice sounds slow.

## Call settings
The same page (http://localhost:7860/voice#settings) tunes barge-in, turn detection (Sarvam server VAD or
local Silero + Smart Turn), TTS voice and chunking, STT, the LLM and silence timeouts. Each value resolves
as page (`bot/config.json`, git-ignored) > `.env` > default, and is read at the start of every call, so
there's no restart. If the bot stops mid-sentence, noise or echo is barging in: turn off *Customer can
interrupt the bot*, or raise the Sarvam VAD threshold and min speech. Schema: `agent/config.py`.

## Local STT: AI4Bharat IndicConformer 600M
Open-source (MIT) ASR for 22 Indian languages that runs on this machine instead of Sarvam's cloud STT
(`agent/stt_indic.py`). It is not streaming: local Silero VAD cuts each customer turn and the model
transcribes it whole, so *Turn detection* switches to Local automatically. Hindi comes back in Devanagari.
1. Accept the terms at https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual (instant).
2. Create a read token at https://huggingface.co/settings/tokens and add `HF_TOKEN=...` to `.env`.
3. `uv sync --extra indic` (torch, transformers, onnxruntime; a plain `uv sync` removes them again).
4. Try it: `uv run python -m agent.stt_indic some.wav` (first run downloads ~2.4 GB, then prints CTC and RNNT).
5. On the /voice page set *STT engine* to IndicConformer (or `STT_PROVIDER=indic` in `.env`).

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
| `agent/config.py` | Tunable call settings (schema, validation, page > .env > default) |
| `agent/voice.py` | Active cloned voice + `SarvamCloneTTSService` (clone websocket) |
| `server.py` | `/exotel` websocket, `/api/offer` WebRTC, `/voice` page + `/api/voices` + `/api/config`, `/health` |
| `static/voice.html` | Voice Studio: record, clone, preview, choose the bot's voice |

Outcomes, flow mode: see `flows/service-scheduling/README.md` (`slot_confirmed`, `site_on_hold`, `rm_callback`, …, plus `hung_up`).
Outcomes, checklist mode: `confirmed`, `callback`, `do_not_call`, `wrong_number` (agent); `no_answer`, `handed_to_human`, `hung_up` (system).
