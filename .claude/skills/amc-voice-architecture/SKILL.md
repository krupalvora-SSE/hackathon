---
name: amc-voice-architecture
description: Stack and pipeline design for the Hindi AMC cleaning phone voice agent. Use when choosing or wiring telephony, STT, LLM, TTS, voice cloning, VAD/turn detection, or when working on latency, streaming, barge-in or cost per call.
---

# Voice Agent Architecture (AMC cleaning bot)

## Target pipeline (streaming end to end)

```
Phone (PSTN) ──> Telephony provider (Exotel / Plivo / Twilio)
                    │  bidirectional WebSocket media stream (8kHz μ-law/PCM)
                    ▼
              Agent server (Python, Pipecat or LiveKit Agents)
   ┌──────────────────────────────────────────────────────────┐
   │ VAD (Silero) → STT streaming (Hindi/Hinglish)            │
   │      → turn detector → LLM (streaming, tool calls)       │
   │      → sentence chunker → TTS streaming (cloned voice)   │
   │ Barge-in: user speech during TTS = cancel TTS + LLM      │
   └──────────────────────────────────────────────────────────┘
                    │ tool calls (HTTP)
                    ▼
              Slot service (FastAPI + SQLite)  ← see slot-booking-tools
```

Use a framework (Pipecat or LiveKit Agents). Do not hand-roll the audio loop in a 24h hackathon; both already handle VAD, interruption, frame pipelines and telephony serializers.

## Component choices (check current docs/pricing before committing)

| Layer | Options to evaluate | What matters for us |
|---|---|---|
| Telephony | Exotel (Voicebot/stream applet), Plivo Audio Streams, Twilio Media Streams | Indian numbers, WebSocket streaming, warm transfer to a human number |
| STT | Sarvam (Saarika / Saaras), Deepgram (Hindi), Google Chirp, AssemblyAI, ElevenLabs Scribe | Hinglish code-mixing, 8kHz phone audio, streaming partials, numbers ("teen hazaar", "pandrah tareekh") |
| LLM | Fast tier: Claude Haiku 5.5 (`claude-haiku-5-5`), GPT mini-class, Gemini Flash | Time to first token < 400ms, reliable tool calling, Hindi output |
| TTS + clone | ElevenLabs (multilingual, instant voice clone), Cartesia (Sonic, clone), Sarvam Bulbul | Time to first audio < 200ms, natural Hindi prosody, clone from 1–2 min of a teammate's voice |
| VAD / turn | Silero VAD + a semantic turn detector (Pipecat smart-turn / LiveKit turn detector) | Don't cut off "haan... wo... kal" pauses, but don't wait 1.5s either |

Run a 30-minute bake-off early: same 10 Hindi utterances recorded over a real phone line through each STT; same 5 agent lines through each TTS. Pick on accuracy + latency, not demos.

## Latency budget (voice-to-voice, measured from end of user speech)

| Stage | Budget |
|---|---|
| Endpointing (VAD silence + turn detector) | 200–300ms |
| STT final transcript | 50–150ms after endpoint |
| LLM time to first token | 200–400ms |
| TTS time to first audio | 100–200ms |
| Network + telephony | 50–150ms |
| **Target p50** | **< 800ms; stretch 500ms** |

Tricks that buy the most:
- Host the agent in an Indian region (Mumbai) close to telephony and vendor endpoints.
- Stream everything: send LLM tokens to TTS at the first clause/sentence boundary (`।`, `,`, `?`).
- Pre-render static audio: greeting, recording disclosure, "ek second, main check karti hoon", goodbye. Play from cache with 0ms TTS.
- Filler while a tool runs: if `check_availability` takes > 300ms, play a cached "ek minute, slots dekh rahi hoon".
- Keep the system prompt short (< 1.5k tokens), put customer context in it once; use prompt caching if the provider supports it.
- Speculatively start the LLM on a stable interim transcript; discard if final transcript differs.
- Keep LLM replies to 1–2 short sentences (enforced in prompt + `max_tokens` ~120).

## Barge-in and audio hygiene
- Enable interruptions: on user speech during bot audio, stop TTS, flush the telephony buffer, cancel the in-flight LLM.
- Ignore backchannels ("haan", "hmm", "achha", "ji") as interruptions if they are < ~600ms; treat them as acknowledgements.
- Phone audio is 8kHz; resample to 16kHz for STT if the vendor expects it. Apply noise suppression (e.g. Krisp/RNNoise filter in Pipecat) before VAD.
- Echo: rely on telephony-side echo cancellation; never feed bot audio back into STT.

## Cost per call (track it, judges score it)
Log per call: STT seconds, LLM input/output tokens, TTS characters, telephony minutes. Compute ₹/call in the post-call record. A 2–3 minute confirmation call should land in single-digit rupees; mention the number in the demo.

## Project layout (suggested)
```
agent/          pipeline, prompts, tools wiring
  bot.py        Pipecat/LiveKit entrypoint
  prompts/      system prompts per flow (see hindi-voice-scripting)
  audio_cache/  pre-rendered static lines
slots/          FastAPI slot service + SQLite (see slot-booking-tools)
eval/           scenarios, simulated caller, latency reports (see voice-agent-eval)
.env.example    every key name, no values
```

## Secrets
All vendor keys in `.env`, loaded with `python-dotenv`; `.env` in `.gitignore`. Never log full keys.
