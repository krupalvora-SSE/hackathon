# Changelog: service-scheduling flow

Bump `version` in `flow.yaml` with every change and add a line here. The version is stamped on every call record (`flow_version`), so results can be compared across flow changes.

## 0.4.0 (2026-11-26, SSE-15527)
From 3 more sample calls (upset customers, repeated calls, a bad line) and a call-script review:
- Upset or complaining customers (`frustrated`): the bot stops the script, apologises once, raises a support ticket (`create_support_ticket`), gives the helpline (`support_number` in policies.yaml, empty until confirmed) and asks once whether to fix the visit. No → `support_ticket`.
- Call limits before dialling (`call_limits`: 24 h between calls, 3 a week) and an apology first when Beat Plan marks the last visit as missed (`prefix_if`).
- Customer can't hear the bot (`cant_hear`): one "can you hear me?", then end and call back (`line_issue`).
- Quick answers that keep the call where it was: change language (Hindi/English; others get "only Hindi or English"), hold on, problem at the site (saved as `site_issues` and in the Beat Plan comment), who is calling, are you a robot, what the visit involves, does someone need to be home.
- Readiness: "not needed here" (e.g. stairs to the roof) counts as ready (`readiness_notes`).
- "Aaj aa jao" gets the minimum-notice line; a date and time of day in one answer ("kal afternoon") no longer asks the time again.
- Engine: per-intent escalation (`escalate_after`/`escalate_to` on any global intent), `then_repeat: false`, and the checker follows escalation paths.

## 0.3.0 (2026-11-26, SSE-15527)
Changes from listening to 4 real sample calls:
- Time windows now match service hours: morning 9–12, afternoon 12–3, evening 3–6; asking for a time outside 9 am–6 pm gets the hours and the question again (`outside_service_hours`).
- "Not needed yet / plan is new" gets one explanation, then `not_needed_now` (NOT_NEEDED, NOT_NEEDED_CLOSE).
- No ladder → "can you arrange one before the visit?" first; yes keeps the booking (`will_arrange` in the record).
- Customer's own date: booked if Beat Plan has that day, otherwise asked for a time of day and saved as a request (`date_requested`, `request_date` action, `requested_slot` in the record).
- Reminder-SMS sentence on the booked line, behind `sms_reminder` in policies.yaml (off until Beat Plan sends it); now a `{sms_line}` placeholder instead of a separate line.
- The bot only mentions a time window if the customer asks about time (`shares_time` on intents, `time_line` on RECAP and CONFIRMED_CLOSE). A plain yes gets the date only, and backup days are offered without a time of day, so the slot can still move within the day. `time_window_told` is in the call record.
- Repaired words broken by the repo-wide main→mai replace (`maintenance`, `options_remaining`, the `main` prompt key, `__main__`).
- New global intent `repeat_request` ("phir se boliye", "kya bola?", "sorry, what?"): the bot repeats the last question in a shorter, simpler form (`repeat_<key>` lines per question in `prompts/`), without leaving the state or counting as `unclear`.
- `unclear` no longer lists "kya?" / "what?" and its `didnt_catch` line no longer asks the customer to repeat before the bot repeats the question itself.

## 0.2.0 (2026-11-26, SSE-15527)
- README diagrams split into an overview plus one diagram per section, with plain-English step titles (`title`), arrow labels (`label` / `labels`) and `sections` in `flow.yaml`; `flow.html` generated as a shareable page.
- Wired into the Python bot (`bot/agent/flow.py`). Added `checklist: readiness_items` on READINESS_CHECK, the global `unclear` intent with escalation after 2 misses, Hindi/English readiness labels, and a shorter price deflection (the bot now repeats the current question after it).
- Merged the Krupal persona from 0.1.1; the remaining feminine Hindi lines are now masculine, and `{bot_name}` uses the active cloned voice's name.

## 0.1.1 (2026-11-26)
- Persona renamed to Krupal (male); Hindi lines use masculine first-person forms (raha/karta/leta hoon).

## 0.1.0 (2026-11-26, TECH-56, now SSE-15527)
- First version: Beat-Plan-driven scheduling with primary slot offer, 4-hour time bands, alternate band and alternate days, readiness check (A-type ladder, water, light), site on hold with explicit consent, recall (stub), RM escalation, DNC, wrong person, and a Beat Plan comment after every call.
