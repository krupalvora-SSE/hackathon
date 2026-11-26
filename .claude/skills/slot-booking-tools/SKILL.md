---
name: slot-booking-tools
description: Backend slot service (availability, hold, book, reschedule, cancel, customer context) and the LLM tool definitions the voice agent calls. Use when building or changing the FastAPI/SQLite slot service, seeding mock data, defining tool schemas, or fixing double-booking and date/slot bugs.
---

# Slot Booking Service + Agent Tools

Hackathon scope: a small FastAPI service with SQLite, seeded with mock customers, technicians and slots. All times are **IST (Asia/Kolkata)**; store ISO dates and `HH:MM-HH:MM` windows.

## Data model
```
customers(id, name, salutation, phone, address, landmark, pincode, city,
          system_size_kw, amc_cleanings_total, amc_cleanings_used, amc_valid_till,
          dnc BOOLEAN DEFAULT 0)
technicians(id, name, cluster_pincodes JSON, phone)
slots(id, date, window, cluster, capacity, booked)          -- window: '07:00-09:00'
holds(id, slot_id, customer_id, expires_at)                 -- TTL 5 min
bookings(id, customer_id, slot_id, status, address_snapshot, landmark,
         cancel_reason, created_at, updated_at, call_id)    -- status: booked|cancelled|rescheduled|completed
call_records(call_id, customer_id, payload JSON, created_at)
```
Slot windows (defaults): `07:00-09:00`, `09:00-11:00`, `16:00-18:00`. Avoid noon windows; panels are hot.
Map pincode → cluster with a simple dict. Seed 14 days, capacity 2–3 per window, some full, so the demo shows "slot full, alternative".

## Endpoints
| Method | Path | Notes |
|---|---|---|
| GET | `/customers/{id}/context` | Everything the prompt needs, incl. existing active booking |
| GET | `/availability?customer_id&from&to&part_of_day` | Returns free windows for the customer's cluster, earliest first, max 6 |
| POST | `/holds` `{customer_id, date, window}` | Atomic: `capacity - booked - active_holds > 0` else 409 with 2 nearest alternatives |
| POST | `/bookings` `{hold_id, landmark?, address_update?, call_id}` | Converts hold → booking, increments `booked`, idempotent on `hold_id` |
| POST | `/bookings/{id}/reschedule` `{hold_id, call_id}` | Frees old slot, books new one in one transaction |
| POST | `/bookings/{id}/cancel` `{reason, call_id}` | Frees slot |
| POST | `/customers/{id}/dnc` | Sets `dnc=1` |
| POST | `/calls/{call_id}/record` | Validated post-call record (schema in `amc-call-flows`) |

Use `BEGIN IMMEDIATE` transactions in SQLite for hold/book to avoid races. Every write takes `call_id` for traceability. Return Hindi-ready fields in availability (e.g. `"label_hi": "बुधवार तीन दिसंबर, सुबह सात से नौ"`) so the LLM reads a label rather than formatting dates itself.

## LLM tools (keep descriptions short; the model reads them every turn)

```json
[
  {"name": "check_availability",
   "description": "Get free cleaning slots for this customer. Call before offering any slot.",
   "input_schema": {"type": "object", "properties": {
      "date_from": {"type": "string", "description": "YYYY-MM-DD"},
      "date_to":   {"type": "string", "description": "YYYY-MM-DD"},
      "part_of_day": {"type": "string", "enum": ["morning", "evening", "any"]}},
    "required": ["date_from", "date_to"]}},
  {"name": "hold_slot",
   "description": "Temporarily hold the slot the customer chose. Returns hold_id or alternatives if full.",
   "input_schema": {"type": "object", "properties": {
      "date": {"type": "string"}, "window": {"type": "string"}}, "required": ["date", "window"]}},
  {"name": "book_slot",
   "description": "Confirm the held slot after the customer agreed to the recap.",
   "input_schema": {"type": "object", "properties": {
      "hold_id": {"type": "string"}, "landmark": {"type": "string"},
      "address_update": {"type": "string"}}, "required": ["hold_id"]}},
  {"name": "reschedule_booking",
   "description": "Move the existing booking to a held new slot.",
   "input_schema": {"type": "object", "properties": {"hold_id": {"type": "string"}}, "required": ["hold_id"]}},
  {"name": "cancel_booking",
   "description": "Cancel the existing booking. Only after the customer clearly said cancel and declined a reschedule.",
   "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
  {"name": "transfer_to_human",
   "description": "Transfer the call to a human service agent.",
   "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
  {"name": "end_call",
   "description": "End the call with the final outcome after saying goodbye.",
   "input_schema": {"type": "object", "properties": {
      "outcome": {"type": "string", "enum": ["confirmed","scheduled","rescheduled","cancelled","callback","no_answer","wrong_number","do_not_call","handed_over"]},
      "callback_at": {"type": "string"}, "cancel_reason": {"type": "string"},
      "spoke_to": {"type": "string", "enum": ["customer","family_member","other"]},
      "sentiment": {"type": "string", "enum": ["positive","neutral","negative"]},
      "notes": {"type": "string"}},
    "required": ["outcome"]}}
]
```
`customer_id`, `booking_id` and `call_id` are injected by the agent server, never asked of the LLM, so it can't hallucinate IDs.

## Tool-call hygiene
- Tool latency budget: < 150ms locally. Play a cached filler line if a call exceeds 300ms.
- On 409 from `hold_slot`, return alternatives in the tool result so the next LLM turn can offer them directly.
- Gate tools by state (see `amc-call-flows`): `book_slot` only after RECAP yes; `cancel_booking` only in CANCEL state.
- Log every tool call + result with timestamps; the eval harness reads these logs.
