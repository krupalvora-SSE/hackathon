# visit-bot (Python, Pipecat 1.17)

Voice agent for solar maintenance visits. Two call engines, picked with `CALL_FLOW` in `.env`:

- **`service-scheduling` (default):** runs the YAML flow in `../flows/service-scheduling/` (SSE-15527). Offers the Beat Plan primary slot, 3-hour time windows, backup days, checks ladder / water / light, puts sites on hold with consent, recall, RM callback, and posts a Beat Plan comment after every call. The LLM only classifies what the customer said (`customer_said(intent, …)`); `agent/flow.py` picks the next step and the exact line to speak. Change behaviour by editing the YAML, not Python.
- **`checklist`:** the original engine. Collects 4 keys
`visit_date`, `visit_time_slot`, `ladder_available`, `water_source_available`.
The Node app in `../app` owns bookings, calls and the dashboard. This bot owns the call audio.

```
Browser mic ──POST /api/offer (WebRTC, 16 kHz)──► bot ──POST events──► app :3000 ──SSE──► dashboard
                                                   └──GET /api/calls/:id (booking)──┘
```

## Run
```bash
cp .env.example .env            # GOOGLE_API_KEY (Gemini)
uv sync --extra whisper         # local Whisper; a plain `uv sync` removes it
uv run --extra whisper uvicorn server:app --port 7860
cd ../app && npm start          # http://localhost:3000 → "Browser" button = mic test
uv run pytest -q                # engine tests
uv run python simulate.py       # text-mode call (or --auto "persona", --booking BK-1003, --flow checklist)
```

## Runs on this machine
Speech recognition and the voice are open source and load from model files on this machine; only the LLM
(Gemini) is a cloud call. Nothing is downloaded at runtime (the server sets `HF_HUB_OFFLINE=1`), so the
model files below must already be in place.

| Part | Engine | Where |
|---|---|---|
| Speech to text | Whisper large-v3-turbo on the Apple GPU (MLX), from the local model cache | `agent/stt_whisper.py` |
| Turn detection | Silero VAD + Smart Turn | `agent/bot.py` |
| Voice | Piper: `hi_IN-jasir-medium` (our trained voice, `voice_training/`, default), Pratham, Rohan; English lines in Ryan/Alan. Files in `~/.cache/pipecat/piper/` | `agent/voice_local.py` |
| My voice (optional) | Piper re-voiced as a recorded sample by OpenVoice V2 | `clone_tts/` (own env, port 7861) |

## My voice
Open http://localhost:7860/voice (bot server running). Read the script for 15–30 s, give the voice a name,
tick consent, **Save recording as my voice**, preview it, then **Use for bot**. Calls then use
"Piper + my voice": Piper speaks and the local converter re-voices it as your sample
(`bot/local_voice.wav`, git-ignored). Start the converter first:
`cd clone_tts && uv run uvicorn server:app --port 7861`. Without it, calls speak plain Piper.

## Call settings
The same page (http://localhost:7860/voice#settings) tunes barge-in, Silero turn detection, the voice,
STT, the LLM and silence timeouts. Each value resolves
as page (`bot/config.json`, git-ignored) > `.env` > default, and is read at the start of every call, so
there's no restart. If the bot stops mid-sentence, noise or echo is barging in: turn off *Customer can
interrupt the bot*, or raise Silero confidence, start and min volume. Schema: `agent/config.py`.

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
| `agent/voice.py` | Which person the voice is (the name the bot introduces itself with) |
| `agent/voice_local.py` | Piper TTS (Hindi + English voices), optional re-voicing through `clone_tts/` |
| `agent/stt_whisper.py` | Local speech recognition (Whisper) |
| `server.py` | `/api/offer` WebRTC, `/voice` page + `/api/local-voice` + `/api/config`, `/health` |
| `static/voice.html` | Voice Studio: record your voice, preview it, use it for calls; call settings |

Outcomes, flow mode: see `flows/service-scheduling/README.md` (`slot_confirmed`, `site_on_hold`, `rm_callback`, …, plus `hung_up`).
Outcomes, checklist mode: `confirmed`, `callback`, `do_not_call`, `wrong_number` (agent); `no_answer`, `handed_to_human`, `hung_up` (system).
