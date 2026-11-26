---
name: amc-call-flows
description: Conversation state machine, required slots, outcomes and the structured post-call record for AMC solar panel cleaning calls (service slot scheduling, slot confirmation, reschedule, cancel). Use when designing, changing or debugging what the agent asks, in what order, and how a call ends.
---

# AMC Cleaning Call Flows

Two outbound flows share one agent; the flow is chosen by the call's `call_type` passed in at dial time.

## Customer context injected at call start
Fetched from the slot service before dialling (`GET /customers/{id}/context`):
`customer_name, preferred_salutation (ji / sir / madam), phone, address, landmark, pincode, city, system_size_kw, amc_plan (cleanings_total, cleanings_used, valid_till), last_cleaning_date, existing_booking (if any), language_pref`.

Never ask the customer for something we already have; **confirm** it instead.

## Flow A — Service slot scheduling (`call_type = schedule`)

```
OPEN ─> IDENTIFY ─> PURPOSE ─> FIND_SLOT ─> CONFIRM_ADDRESS ─> PREP_REMINDERS ─> RECAP ─> CLOSE
```

1. **OPEN**: greet by name, say who we are, recording disclosure (one sentence). Example in `hindi-voice-scripting`.
2. **IDENTIFY**: "Kya meri baat {name} ji se ho rahi hai?" Branches: wrong person / family member / wrong number → see `call-robustness-handoff`.
3. **PURPOSE**: "Aapke AMC plan mein solar panel cleaning due hai ({used}/{total} ho chuki hain)." Ask if now is a good time (2 sec, not a speech).
4. **FIND_SLOT**: ask preferred day, then offer **max 2 concrete options** from `check_availability`. Never read out a list of 6 slots on the phone.
   - Prefer morning (7–10) or evening (4–6) slots; panels are cool and the technician can work safely. Explain only if the customer asks for afternoon.
   - Customer gives a vague answer ("agle hafte kabhi bhi") → pick the earliest two and offer them.
   - Customer picks something unavailable → "Us din slot full hai, {alt1} ya {alt2} chalega?"
   - Once chosen → `hold_slot` immediately (5 min TTL) so it is not lost while we confirm address.
5. **CONFIRM_ADDRESS**: read back address short form + landmark; ask for a landmark if missing. Accept corrections, store them in `address_update`.
6. **PREP_REMINDERS** (keep it to one sentence): someone at home, roof/terrace access open, water tap available near the panels.
   Note: the organisers' spec for flow 5 says "remind to keep electricity bill handy". That is a survey-call line; ask organisers whether to keep it for cleaning. Default: include it only if they confirm.
7. **RECAP**: "Toh {weekday}, {date}, {slot} — {landmark} ke paas. Theek hai?" → on yes, `book_slot(hold_id)`.
8. **CLOSE**: thank, say an SMS/WhatsApp confirmation will come (only if we actually send one), end with `end_call(outcome, record)`.

## Flow B — Service slot confirmation (`call_type = confirm`, day before / morning of)

```
OPEN ─> IDENTIFY ─> READ_BOOKING ─> {CONFIRM | RESCHEDULE | CANCEL} ─> REMINDERS ─> CLOSE
```
- READ_BOOKING: "Kal {date} ko {slot} ke beech hamare technician aapke ghar solar panel cleaning ke liye aayenge." Then confirm address + landmark.
- CONFIRM → reminders → close.
- RESCHEDULE → same as Flow A step 4 → `reschedule_booking(booking_id, new_hold_id)`.
- CANCEL → ask reason once, gently ("Koi khaas wajah?"), offer reschedule once, if still cancel → `cancel_booking(booking_id, reason)`. Do not argue.

## Outcomes (exactly one per call)

| `outcome` | When |
|---|---|
| `confirmed` | Flow B, existing booking confirmed |
| `scheduled` | Flow A, new booking created |
| `rescheduled` | Booking moved; `new_slot` required |
| `cancelled` | Booking cancelled; `cancel_reason` required |
| `callback` | Customer busy / asked to call later; `callback_at` if given |
| `no_answer` | No pickup, voicemail, or silence on line after 2 prompts |
| `wrong_number` | Person is not the customer and doesn't know them |
| `do_not_call` | Customer asks not to be called; stop immediately |
| `handed_over` | Transferred to human; `handover_reason` required |

## Post-call record (what judges check — must be correct every time)

```json
{
  "call_id": "uuid",
  "customer_id": "C123",
  "call_type": "schedule | confirm",
  "outcome": "scheduled",
  "booking_id": "B456",
  "slot": {"date": "2026-12-03", "window": "07:00-09:00"},
  "new_slot": null,
  "cancel_reason": null,
  "callback_at": null,
  "address_confirmed": true,
  "address_update": null,
  "landmark": "Hanuman mandir ke saamne",
  "spoke_to": "customer | family_member | other",
  "reminders_given": ["person_at_home", "roof_access", "water_tap"],
  "handover_reason": null,
  "customer_sentiment": "positive | neutral | negative",
  "notes": "free text, Hinglish ok",
  "transcript_url": "...",
  "metrics": {"duration_s": 94, "p50_latency_ms": 620, "cost_inr": 4.1}
}
```

Rules:
- The record is written by **code from tool results**, not free-typed by the LLM. The LLM only supplies `outcome`, reasons and notes via `end_call`; `slot`, `booking_id` come from the booking API response.
- Validate with Pydantic before saving. If validation fails, log and mark `needs_review: true`.
- Show the record live on a small dashboard during the demo — it proves "job done".

## Implementing the state machine
Keep one system prompt per flow with the steps listed as a checklist and the current state tracked in code (`state` field injected into each turn's context), rather than one giant prompt. Pipecat Flows or a simple dict-of-states with per-state instructions + allowed tools both work. Each state exposes only the tools it needs (e.g. `book_slot` only in RECAP), which stops the LLM booking early.
