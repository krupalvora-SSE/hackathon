# Changelog: service-scheduling flow

Bump `version` in `flow.yaml` with every change and add a line here. The version is stamped on every call record (`flow_version`), so results can be compared across flow changes.

## 0.20.0 — bot follow-up 3 (2026-11-27)
From test call CALL-2c3e81bb (ended as do-not-call) and an LLM benchmark:
- Bot: Whisper's doubt about a turn is kept until the LLM has acted on it. When the customer went on talking before the reply, the doubt of three garbled segments ("प्रवाइब नियुक्त …") was wiped and the LLM read them as `do_not_call`; that turn is now asked again.
- "बार से ती" / "बाराई से ती" are read as the offered 12-3 window.
- `will_arrange` only counts for the item the customer lacks (the LLM repeats it on later turns; water and light they have stay "ready"), and not when they name a date after the visit ("2 weeks mein" for a visit tomorrow is missing -> site on hold).
- Latency: the "जी, एक second" filler is injected between the LLM and the voice, so it plays while Gemini is thinking (it used to wait behind the LLM and come out glued to the reply); filler delay 1.0 s -> 0.5 s; "हाँ जी…" filler replaced by a neutral "जी…".
- No filler when Whisper is unsure of the turn (CALL-392c194e): the reply is "माफ़ कीजिए, ठीक से सुनाई नहीं दिया", and a filler before it sounded like the bot had understood.
- Filler "जी, एक second" -> "जी, एक मिनट": "सेकंड" is not in the Hindi voice's training lines and came out Hindi-accented.
- "Hello?" was said twice: the browser transport reported one connection twice (CALL-24b72b22); the connect handler now runs once.
- At the identity question a garbled reply that opens with a clear "हाँ" and has no "no" or hedge is a yes ("हाँ, सोल पालोग?" CALL-24b72b22, "हाँ बुल रिम।" CALL-392c194e); it used to be asked again.
- The reply to "Hello?" (answered by the greeting, never by the LLM) is now done with once greeted: a misheard "प्रोग" kept the turn unsure and the clear "हाँ" after the greeting was asked again (CALL-1e03b45f).
- Filler "जी, देखता हूँ" -> "एक मिनट रुकिए": "देखता" is not in the Hindi voice's training lines and came out garbled right before "आपकी visit … confirm हो गई है" (CALL-7b3aafb3). A test now keeps every filler to trained words.
- Time windows said with a dash ("12-3", "9 – 12") and "बार से एतीन" now match the offered window; both were asked again and the third miss sent the call to a senior (CALL-f7139d1d).
- LLM benchmark (real prompt and tools, 14 simulated customers x 2): gemini-3.6-flash ~1.5 s, no wrong outcomes; gemini-3.5-flash-lite ~0.72 s but 4/28 wrong (read "date perfect hai par ladder nahi hai" as wrong person, booked a no-ladder site); gemini-3.1-flash-lite 6/28 wrong; gemini-3.7/3.8-flash ~3 s. Kept gemini-3.6-flash.

## 0.20.0 — bot follow-up 2 (2026-11-27)
From test call CALL-daa8ba9d (booked): "पानी का source है तो नहीं पर मिल जाएगा" was taken as a plain yes and the recap said "पानी ready है".
- New entity `will_arrange` on the ladder / water / light questions: "not there now, but I'll have it by the visit" is recorded as an item the customer will arrange (`will_arrange` in the call record, `arranging` on the Beat Plan readiness event), and the recap says "पानी का इंतज़ाम आप visit से पहले कर लेंगे".

## 0.20.0 — bot follow-up (2026-11-27)
From test call CALL-5b614da9, where "is there water near the panels?" was asked three times ("येस, है." / "हाँ मिजया लाँ" / "हाम में जाइबा", each read by the LLM as yes but flagged unsure by Whisper) and the call went to a senior callback:
- Outside the booking steps (RECAP, HOLD_CONSENT), an unsure turn that opens with a plain yes and has no "no" or "शायद" is acted on; RECAP still reads every answer back.
- Outside the booking steps, the same LLM reading of two unsure turns in a row at the same step is taken instead of asking a third time.
- "येस" / "यस" count as a clear yes.

## 0.20.0 (2026-11-27)
From test call CALL-418d20ee, where the customer picked "सोमवार, 30 नवंबर" and Whisper heard "सुमार थीस लंड", so the bot said "ठीक से सुनाई नहीं दिया" twice:
- Bot: at ALTERNATE_DAYS, a turn that would be asked again is matched by sound against the days just offered (weekday names and day of the month, with aspirates and vowels ignored: "सुमार थीस" -> Monday 30th). Only when exactly one offered day fits and the turn has no "no"; number words below 11 ("दो" in "कर दो") never count.
- ASK_SLOT names the day it asks about ("सोमवार, 30 नवंबर को आपको कौन सा time ठीक रहेगा?"), so a wrong guess is heard and can be corrected with the right day.

## 0.19.1 (2026-11-27)
From test call CALL-fc2f980f (booked correctly, but RECAP took three tries):
- English time lines say "between": the RECAP re-ask was "Saturday, 28 November, 12 pm and 3 pm. Shall I confirm?"; TIME_BAND's exact-time lines read "Does 9 am and 12 pm work?".
- Bot: a re-ask after an unsure transcript keeps the call's language (a garbled "Yes, I'm from India." switched it to English mid-call), and an unsure "हाँ, confirm …" (an opening yes plus "confirm", no "no") is acted on instead of asked again.

## 0.19.0 (2026-11-27)
From a test call where the customer picked मंगलवार, 1 दिसंबर from the backup days, said no to its time window, and the bot hung up with "अभी हमारे पास और slots नहीं हैं":
- A "no" to the time of a day picked from the offered days rolls back to the day question: the other days offered with it (गुरुवार, 3 दिसंबर) are offered again; the rejected day is not.
- New step ASK_OWN_DATE: when no Beat Plan day is left (none in Beat Plan, a no to every offered day, or to the time of the last one), the customer is asked for a date of their own (free day -> ASK_SLOT, other day -> ASK_BAND_FOR_DATE) instead of closing with no slot. A no there still closes as `no_slot_available`.
- Dates are understood in more forms: "30 तारिक" / "तारिख" / "तारीक" (Whisper spellings), Hindi number words up to 31 ("तीस तारीख"), and "30 को" / "30 चाहिए". Before, "मुझे 30 तारिक चाहिए" got "date समझ नहीं आई".
- "एक भी नहीं" / "ek bhi nahi" / "dono nahi" added to `deny` examples (none of the offered days).

Bot (STT), same day: a Whisper repetition loop of a filler sound ("जी, जी, जी, ... ज" from 1.3 s of audio, CALL-980a5cd0) is dropped instead of being read as a yes at WRONG_PERSON; other loops collapse to one copy ("नहीं नहीं नहीं" -> "नहीं"). "एक बिने" is read as "एक भी नहीं".

## 0.18.0 (2026-11-27)
From an end-to-end test run of simulated callers:
- HOLD_CONSENT takes `proposes_date`: "haan, Monday ko karwa lo" at the hold question books that day (same branches as ALTERNATE_DAYS) instead of pausing the site. A yes whose date differs from the hold date counts as a proposed day, and the hold is dropped from the call record.
- Call later with the time in the same turn ("kal 10:15 baje call karna") skips "आपको किस time पर call करना ठीक रहेगा?" and schedules the recall directly. Vague times ("baad mein") still get the question.
- RECALL_CLOSE says the call-back time built by code ("कल सुबह सवा दस बजे", "tomorrow at 10:15 am") instead of reading back the customer's romanised words; unparsed times become `_global.recall_as_told` ("आपके बताए time पर").

## 0.17.0 (2026-11-27)
From call CALL-8dc7d921, where the customer said yes to the date and the visit was booked without any time being asked:
- New step ASK_SLOT after the customer agrees to a day (the planned date, a backup day, their own Beat Plan day, or after NOT_NEEDED): "उस दिन आपको कौन सा time ठीक रहेगा? सुबह नौ से बारह बजे या दोपहर बारह से तीन बजे?" Only the bands Beat Plan has free that day are offered (`{bands_spoken}`); with one free band the bot names it and asks if it's okay (`one_band`).
- New system step CHECK_SLOT checks the band they pick; a full band gets the free ones offered again (`band_full`). "Any time" / yes keeps the planned band; "no time that day" goes to ALTERNATE_DAYS; a clock time ("11 baje") counts as its band.
- This replaces the 0.10 rule (SSE-15527) of mentioning a time only when the customer asks: the recap and "visit booked" lines now always say the time window (ASK_SLOT has `shares_time: true`).

## 0.16.0 (2026-11-27)
- Greeting no longer says the call is recorded ("यह call record हो रही है।" / "This call is being recorded." removed); `compliance.recording_disclosure` is false. The call is still recorded for the dashboard.

## 0.15.0 (2026-11-26, SSE-15557)
From a call where the customer told us of a death in the family and the bot kept asking when to call back:
- New global intent `sensitive_situation` (death, funeral, accident, hospital / serious emergency): a sincere condolence, the call ends at once (new terminal SENSITIVE_CLOSE), no question and no date asked. Calls are paused for `policies.sensitive_pause_days` (14) and recorded as `site_on_hold` with reason `bereavement_or_emergency`, `hold.until` and consent.
- New global intent `upset_customer` ("आप में कोई sensitivity है?"): an apology instead, the call ends, calls paused (reason `customer_upset`).
- `personal_reason` is now only for mild reasons (fever, a family function) and still asks when to call back; "emergency" and "hospital" moved to `sensitive_situation`. The classifier is told that a death is always `sensitive_situation`.
- "कभी मत करिए" / "मत कीजिए call" added to the `do_not_call` examples.

## 0.14.0 (2026-11-26, SSE-15557)
From a fifth sample call:
- Relative durations are understood as dates: "1 महीने बाद", "15 दिन बाद", "दो हफ़्ते बाद", "after 1 month", "next week / month" (a month counts as 30 days). Before, "when will the site be ready?" looped three times on "1 महीने बाद".
- A heard-but-unusable answer (no usable date, time of day or option) now counts toward the same escalation as "not understood": after two repeats a senior is asked to call (reason `repeated_misunderstanding`) instead of asking forever. Date failures use a line that says what is expected ("जैसे 5 दिसंबर, या एक महीने बाद?").
- A hedge inside a yes ("जी हाँ, फिर तो शायद है") is a maybe: new `hedged` entity on `customer_said`, treated as `unsure` at the readiness questions.
- A customer can correct a readiness answer at the recap or the "what to change?" step ("मेरे पास ladder नहीं है"): new intent `corrects_readiness` re-runs the checklist with the corrected answer (a ladder that is now missing asks "can you arrange one?" first). Before it was misread as "change the day".
- The recap is a proper sentence: "ladder और पानी ready हैं, light का इंतज़ाम आप visit से पहले कर लेंगे" (no stray "और ... और ..."); lists read "a, b और c".

## 0.13.0 (2026-11-26, SSE-15557)
From a fourth sample call:
- "Maybe" (शायद / हो सकता है, intents `unsure` and `dont_know`) to the ladder, water or light question is no longer booked as ready: the bot asks once whether they will arrange it before the visit (`arrange_<item>` for all three items). Yes is recorded as `will_arrange`, no or another maybe as missing (hold flow). The recap now says which items the customer will arrange (`{ready_note}`) instead of claiming everything is ready.
- New global intent `asks_meaning` ("ये A type ladder क्या होता है?"): the bot explains the item being asked (`explain_ladder / water / light`) and asks again, instead of "could not hear".
- Beat Plan slots on a day we do not visit (Sunday) are dropped before offering; the next valid slot becomes the plan, and a call with none is not placed (`blocked_reason: no_visit_day_slot`). `confirm_slot` refuses a non-visit day so the team confirms by hand.
- A family member is spoken to as family: `main_family` lines for the offer ("Amit जी के solar system…", "घर पर कोई available रहेगा?") and the goodbye ("Amit जी की visit…").
- Shorter greeting, recording disclosure kept: "नमस्ते, क्या {name} जी बोल रहे हैं? मैं SolarSquare से {bot_name}। यह call record हो रही है।"

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
