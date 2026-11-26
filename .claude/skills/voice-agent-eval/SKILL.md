---
name: voice-agent-eval
description: Testing and demo prep for the Hindi AMC voice agent — scenario suite, LLM-simulated callers, text-mode tests, replaying real recordings, latency and cost measurement, outcome accuracy scoring, and the live demo plan. Use when writing tests, measuring latency, checking outcome correctness, or preparing the presentation.
---

# Evaluation and Demo Prep

"We will judge calls, not slides." Measure the same things the judges score: outcome correctness, latency, naturalness, robustness, cost.

## Three test layers (build in this order)
1. **Text mode (fastest loop)**: run the agent's LLM + tools with typed Hinglish input in a CLI (`python -m agent.text_chat --scenario S07`). Catches 80% of flow and tool bugs with no audio cost.
2. **Simulated caller**: a second LLM plays the customer from a persona card (below), conversing with the agent in text; then optionally via TTS→STT to include speech errors. Run all scenarios in batch; assert on the final record.
3. **Real phone calls**: teammates call/receive on test numbers, follow scenario cards, including noise (fan, traffic video playing, speakerphone). Record every call.

Also replay snippets from the provided real recordings through STT to measure transcription accuracy on real customer speech (numbers, dates, names).

## Scenario card format
```yaml
id: S07
flow: schedule
persona: "Retired man, Pune, speaks slow Hindi, says 'mere bete se poochna padega' first, then agrees to Saturday morning"
customer_id: C003
noise: none | traffic | tv | speakerphone
behaviours: [interrupts_recap, long_pause_5s]
expected:
  outcome: scheduled
  slot.window: "07:00-09:00"
  slot.weekday: Saturday
  reminders_given: contains roof_access
  spoke_to: customer
```
Starter scenario list: `references/scenarios.md`.

## Automatic checks per call
- `outcome` matches expected (exact).
- Booking in DB matches the record (date, window, status); no orphan holds; no double booking.
- Compliance: transcript contains recording disclosure; no digits/currency amounts in agent turns (regex for `₹|rupay|rupees|\d+ ?%`); DNC respected.
- Turn count and duration (shorter is better for confirm calls: target < 90s).

## Latency measurement
Log timestamps per turn: `user_speech_end`, `stt_final`, `llm_first_token`, `tts_first_audio`, `audio_out_start`. Report p50/p90 voice-to-voice per call and per stage. Pipecat/LiveKit expose metrics events (TTFB per service); enable them. Show p50 on the demo dashboard.

## Cost per call
Sum per call: STT audio seconds × rate, LLM tokens × rate, TTS characters × rate, telephony minutes × rate. Put rates in `eval/rates.yaml`. Report ₹/call and ₹/1000 calls.

## Naturalness check
Blind test: play 3 bot calls and 3 human calls (teammate reading the same script) to people outside the team; ask "human or bot?". Report the fool rate. Tune TTS speed/stability and add natural fillers ("जी", "अच्छा") if the bot gets spotted on pacing.

## Demo plan (live, ~5 minutes)
1. One-line problem + architecture slide (10 seconds max).
2. **Live call 1**: happy path scheduling on a judge's or teammate's phone, dashboard showing the transcript, tool calls and the structured record filling in live.
3. **Live call 2**: confirmation call where the "customer" interrupts, says "kal nahi, parso shaam ko", agent reschedules.
4. **Robustness burst**: "ask my son" → callback; "call mat karo" → DNC; angry/technical → handover.
5. Numbers: outcome accuracy over N simulated calls, p50 latency, ₹/call.
Have a pre-recorded backup video of each live call in case the network fails.
