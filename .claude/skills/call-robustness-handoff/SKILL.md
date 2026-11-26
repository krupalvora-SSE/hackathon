---
name: call-robustness-handoff
description: Handling hard call situations for the Hindi AMC voice agent — silence, interruptions, noise, wrong person / "ask my son", wrong number, do-not-call, angry customers, off-topic questions, human handover and compliance rules. Use when implementing or testing edge-case behaviour, handover, or the recording/no-promise rules.
---

# Robustness, Handover and Compliance

Judges will deliberately test: code-mixing, interruptions, silence, noise, and "ask my son". Each case below needs a coded behaviour, not just a prompt line.

## Silence
| Silence | Action |
|---|---|
| 4–5s after bot question | Gentle re-prompt: "हैलो, क्या आप मुझे सुन पा रहे हैं?" |
| 2nd silence | Shorter re-prompt, rephrase the question more simply |
| 3rd silence / ~15s total | "लगता है आवाज़ नहीं आ रही, हम आपको दोबारा कॉल करेंगे।" → `end_call(no_answer)` (or `callback` if they spoke earlier) |
| No speech from pickup within 6s | Treat as voicemail/no one → `no_answer` |
Implement with a user-idle timer in the pipeline (Pipecat `UserIdleProcessor` or equivalent), reset on any user speech.

## Interruptions (barge-in)
- Stop TTS within ~200ms of real user speech; cancel the pending LLM response.
- Do not repeat the whole interrupted sentence; respond to what the user said.
- Backchannels ("haan", "hmm", "ji") during bot speech are not interruptions; keep talking.
- If the user interrupts the recap with a correction, update and re-recap only the changed part.

## Noisy line / low STT confidence
- Noise suppression before VAD. Raise VAD threshold slightly for phone audio.
- If transcript is empty, garbled, or confidence is low for a **date, time or yes/no that changes the booking**: ask again, do not guess. "माफ़ कीजिए, आपने बुधवार कहा या गुरुवार?"
- After 2 failed attempts on the same item: offer the earliest slot as a yes/no question (yes/no survives noise better than open answers).
- After 3: `transfer_to_human` or `callback`.

## Wrong person / "ask my son"
- Family member picks up: "क्या आप शर्मा जी के घर से बोल रहे हैं?" If household member and willing → may confirm/reschedule an existing booking (note `spoke_to=family_member`). For a new booking, OK if they confirm they're at that address.
- "Mere bete se baat karo" / "wo decide karega": ask for a good time or his number → `callback` with `callback_at` and note. Never pressure.
- Doesn't know the customer → `wrong_number`, apologise, end. Do not reveal booking or address details to an unknown person.

## Do-not-call and stop
Any of "call mat karo", "DND", "band karo", "unsubscribe", "pareshan mat karo": apologise once, `POST /customers/{id}/dnc`, `end_call(do_not_call)`. No retention attempt. Check `dnc` before dialling.

## Off-topic / out of scope
| Topic | Handling |
|---|---|
| Price, charges, extra cost | No numbers. "जो AMC में शामिल है वही होगा, बाकी डिटेल टीम शेयर करेगी।" |
| Generation low, inverter error, damage, leakage | Don't troubleshoot. Offer human handover / ticket → `handed_over` |
| New solar / upgrade / referral | Note it as a lead in `notes`, say team will call; continue the AMC task |
| "Are you a robot?" | Be honest: say it's an AI assistant from SolarSquare and offer a human if they prefer. Never claim to be human. |
| Abusive | One calm line, then end politely |

## Human handover
Trigger `transfer_to_human` when: customer asks for a person; 2 consecutive misunderstandings on the same step; complaint/technical issue; angry sentiment persists; language not supported.
- Say: "मैं आपको अपनी टीम के साथी से जोड़ रही हूँ, एक मिनट।"
- Do a warm transfer via telephony (Exotel/Plivo/Twilio transfer to the configured human number) and pass a one-line summary (name, flow, what's done, why transferred) to the human (dashboard or WhatsApp to the agent's test number).
- If no human available (demo): say the team will call back within X hours, outcome `handed_over` with reason.

## Compliance checklist (organiser rules — every call)
- [ ] Recording disclosure in the opening.
- [ ] Stops when asked; DNC honoured and persisted.
- [ ] No promises of price, savings, generation, or subsidy amounts.
- [ ] No customer data revealed before identity is confirmed.
- [ ] Honest about being AI if asked.
- [ ] Only test numbers dialled (enforce an allowlist in code: refuse to dial numbers not in `ALLOWED_TEST_NUMBERS`).
