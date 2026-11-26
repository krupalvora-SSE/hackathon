# Changelog: service-scheduling flow

Bump `version` in `flow.yaml` with every change and add a line here. The version is stamped on every call record (`flow_version`), so results can be compared across flow changes.

## 0.3.0 (2026-11-26, SSE-15527)
Changes from listening to 4 real sample calls:
- Time windows now match service hours: morning 9–12, afternoon 12–3, evening 3–6; asking for a time outside 9 am–6 pm gets the hours and the question again (`outside_service_hours`).
- "Not needed yet / plan is new" gets one explanation, then `not_needed_now` (NOT_NEEDED, NOT_NEEDED_CLOSE).
- No ladder → "can you arrange one before the visit?" first; yes keeps the booking (`will_arrange` in the record).
- Customer's own date: booked if Beat Plan has that day, otherwise asked for a time of day and saved as a request (`date_requested`, `request_date` action, `requested_slot` in the record).
- Reminder-SMS sentence on the booked line, behind `sms_reminder` in policies.yaml (off until Beat Plan sends it); now a `{sms_line}` placeholder instead of a separate line.
- The bot only mentions a time window if the customer asks about time (`shares_time` on intents, `time_line` on RECAP and CONFIRMED_CLOSE). A plain yes gets the date only, and backup days are offered without a time of day, so the slot can still move within the day. `time_window_told` is in the call record.
- Repaired words broken by the repo-wide main→mai replace (`maintenance`, `options_remaining`, the `main` prompt key, `__main__`).

## 0.2.0 (2026-11-26, SSE-15527)
- README diagrams split into an overview plus one diagram per section, with plain-English step titles (`title`), arrow labels (`label` / `labels`) and `sections` in `flow.yaml`; `flow.html` generated as a shareable page.
- Wired into the Python bot (`bot/agent/flow.py`). Added `checklist: readiness_items` on READINESS_CHECK, the global `unclear` intent with escalation after 2 misses, Hindi/English readiness labels, and a shorter price deflection (the bot now repeats the current question after it).
- Merged the Krupal persona from 0.1.1; the remaining feminine Hindi lines are now masculine, and `{bot_name}` uses the active cloned voice's name.

## 0.1.1 (2026-11-26)
- Persona renamed to Krupal (male); Hindi lines use masculine first-person forms (raha/karta/leta hoon).

## 0.1.0 (2026-11-26, TECH-56, now SSE-15527)
- First version: Beat-Plan-driven scheduling with primary slot offer, 4-hour time bands, alternate band and alternate days, readiness check (A-type ladder, water, light), site on hold with explicit consent, recall (stub), RM escalation, DNC, wrong person, and a Beat Plan comment after every call.
