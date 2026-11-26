# Changelog: service-scheduling flow

Bump `version` in `flow.yaml` with every change and add a line here. The version is stamped on every call record (`flow_version`), so results can be compared across flow changes.

## 0.12.0 (2026-11-26, SSE-15557)
From three sample calls and two requests:
- Fix: the Devanagari "सुबह / शाम / दोपहर" were never understood (the word splitter dropped vowel signs), so the band question looped. A clock time outside the service hours at the band question ("सुबह 7:00 बजे") now gets "visits are 9 to 6" and the question again.
- Re-asks (not understood, off-topic, identity, small talk, silence) use the short form of the question, so the greeting's recording disclosure and the long offer are no longer repeated. Short forms added for ASK_BAND_FOR_DATE and NOT_NEEDED.
- New global intents: `personal_reason` (ill / family matter: sympathy, then "when should we call?" in RECALL), `complaint` (apology, then a senior callback with reason `complaint`), `small_talk` (brief friendly reply, then the question), `other_language` (any language other than Hindi / English: "I don't understand this language, could you speak Hindi or English?"; after two more turns in that language a senior is asked to call, reason `language_not_supported`).
- GREET accepts `household_member` ("I'm his brother, go ahead") and goes straight to the offer.
- Weekday words become dates: every day mentioned is read ("agle somwar ya ravivar"), Sunday is skipped, the first visit day is proposed. A request for a non-visit day gets "visits are Monday to Saturday only" (policy `visit_days`).

## 0.11.0 (2026-11-26, SSE-15551)
- Fixes a loop seen on a call: after "main Sneha nahi hoon" the bot asked "are you from Sneha's home?", and "nahi" / "wrong number hai" were read as `wrong_person`, which re-entered WRONG_PERSON and repeated the question forever. New global intent `wrong_number` closes the call as a wrong number from any step; in WRONG_PERSON `wrong_person` is handled locally and also closes as a wrong number, and a plain "nahi" (deny) closes as before.
- Engine safety net: a global intent that would only re-enter the current step is treated as `unclear`, so it escalates to a senior after two misses instead of repeating.
- Classifier: 6 Hindi and 3 English examples per intent now reach the model (was 3 and 2), Devanagari "हाँ" / "नहीं" lead the yes / no examples, and a hint covers yes/no questions about who is on the line.

## 0.10.0 (2026-11-26, SSE-15554)
- If saving a call-back time (APPLY_RECALL) or creating the senior's callback (ESCALATE_RM) fails, the call now ends in MANUAL_CONFIRM_CLOSE ("the team will confirm") instead of telling the customer "we will call you" / "a senior will call". This matches BOOK and APPLY_HOLD.

## 0.9.0 (2026-11-26, SSE-15552)
- "What time?" is answered at any step. `ask_time` is now a global intent: the bot tells the time window of the chosen day (or the 9 am-6 pm service hours while no day is chosen yet) and repeats the current question. OFFER_PRIMARY and NOT_NEEDED keep their own move to TIME_BAND (`disable_global: [ask_time]`).
- New `asks_time` entity on `customer_said` for replies that answer and ask at once ("chalega Shukrawar, but kya time hoga?"): the time answer is put in front of the next line, whatever step the call moves to, and the recap then mentions the window. Before, the second half was dropped, and a repeated question at the ladder step was read as "no ladder".

## 0.8.0 (2026-11-26, SSE-15551)
Fixes from an audit of the flow's transitions:
- Changing the day or time from the recap (new flag `readiness_done`) goes back to RECAP, keeps the day the customer chose and no longer re-asks the ladder / water / light questions. Before, accepting the time window re-selected the planned slot and could book the wrong day.
- "No" at the recap goes to a new step RECAP_CHANGE ("what would you like to change, the day or the time?").
- "No" at the greeting goes to WRONG_PERSON; a plain "haan" in WRONG_PERSON goes to the offer (family member).
- OFFER_PRIMARY accepts `wants_other_band` and `wants_exact_time` (counted toward the exact-time limit), as TIME_BAND does.
- The exact-time line said "नहीं दे सकती" (feminine); it is now "सकता" like the rest of the male persona.
- New local intent `dont_know` ("pata nahi", "abhi nahi bata sakta"): in ASK_READY_DATE, SITE_ON_HOLD and RECALL it goes to a senior callback (`rm_callback_reason: customer_unsure`); in ASK_BAND_FOR_DATE it closes the date request without a time of day.

## 0.7.0 (2026-11-26, SSE-15527 PR #4, merged in SSE-15542)
Changes from listening to 4 real sample calls (developed as 0.3.0 on its own branch; renumbered when merged after 0.4.0-0.6.0):
- Time windows now match service hours: morning 9–12, afternoon 12–3, evening 3–6; asking for a time outside 9 am–6 pm gets the hours and the question again (`outside_service_hours`).
- "Not needed yet / plan is new" gets one explanation, then `not_needed_now` (NOT_NEEDED, NOT_NEEDED_CLOSE).
- No ladder → "can you arrange one before the visit?" first; yes keeps the booking (`will_arrange` in the record).
- Customer's own date: booked if Beat Plan has that day, otherwise asked for a time of day and saved as a request (`date_requested`, `request_date` action, `requested_slot` in the record).
- Reminder-SMS sentence on the booked line, behind `sms_reminder` in policies.yaml (off until Beat Plan sends it); now a `{sms_line}` placeholder instead of a separate line.
- The bot only mentions a time window if the customer asks about time (`shares_time` on intents, `time_line` on RECAP and CONFIRMED_CLOSE). A plain yes gets the date only, and backup days are offered without a time of day, so the slot can still move within the day. `time_window_told` is in the call record.
- Repaired words broken by the repo-wide main→mai replace (`maintenance`, `options_remaining`, the `main` prompt key, `__main__`).
- Merge fixes (SSE-15542) so PR #4 and the 0.3.0-0.6.0 changes work together: a missing ladder given up front (several answers in one turn) still asks "can you arrange one before the visit?" first; "phir se boliye" in that question repeats it in short form (`repeat_arrange_ladder`); the RECAP repeat line mentions the time window only when the customer asked about time (`repeat_main` / `repeat_with_time`).

## 0.6.0 (2026-11-26, SSE-15539)
- Several answers in one turn: `customer_said` takes a `readiness` entity ({ladder, water, light} booleans). READINESS_CHECK records those items (including missing ones and the dashboard events) and only asks the rest; if all are answered it goes straight on, and a missing item still leads to the hold flow. Unknown items and non-boolean values are ignored.

## 0.5.0 (2026-11-26, SSE-15538)
- New global intents `who_are_you`, `number_source` and `is_robot` ("kaun bol raha hai?", "number kahan se mila?", "aap robot ho?"): short honest answers (SolarSquare and the maintenance topic; number comes from the service records; yes, an AI voice assistant), then the current question is repeated. No prices or promises.

## 0.4.0 (2026-11-26, SSE-15537)
- New global intent `wait_a_moment` ("ek minute ruko", "hold on"): the bot says "main line pe hoon" and does not repeat the question. The silence reprompt is delayed to `policies.silence.hold_s` (25 s) instead of 5-6 s, and goes back to normal when the customer speaks or the first reprompt fires.

## 0.3.0 (2026-11-26, SSE-15527)
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
