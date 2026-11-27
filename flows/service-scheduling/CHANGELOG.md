# Changelog: service-scheduling flow

Bump `version` in `flow.yaml` with every change and add a line here. The version is stamped on every call record (`flow_version`), so results can be compared across flow changes.

## 0.22.0 (2026-11-27, SSE-15551)
From a call where "पानी का source क्या होता है?" was answered with an approved answer about the socket distance ("30 ft ke andar ... socket chahiye"):
- The model prompt now says an approved answer is only for exactly the same question; what ladder / water / light means or why it is needed is always `asks_meaning`.
- `explain_water` now says what a water source is (a tap or tank near the panels), not only why water is needed.

## 0.21.0 (2026-11-27, SSE-15551)
From a call where the offer named only the day, and "जी क्या slot है?" was taken as a yes and the bot went straight on to the readiness questions:
- The planned-day offer now also says the time window ("बुधवार, 2 दिसंबर को शाम तीन से छह बजे के बीच...").
- At that offer a yes that also asks about the time ("जी, क्या slot है?") is treated as the time question: the bot tells the window and asks if it suits.

## 0.20.0 (2026-11-27, SSE-15551)
From a call where a bare "Yes." at the recap made the bot ask the site question in English, and a reported rusting was not acknowledged:
- After the greeting, a bare yes / no / maybe / don't know no longer switches the language; only a fuller answer can.
- When the customer told us about a site problem, the closing line adds "I have noted the problem you told me about, and our Service Hero will check it" (`{issue_line}`, from `_global.issue_noted`).

## 0.19.0 (2026-11-27, SSE-15551)
- New last question before booking: after the customer says yes to the recap, `SITE_ISSUES` asks "is there any problem on your site, like rusting?". A "yes" without detail goes to `ISSUE_DETAILS` ("which problem?"); a "no" or "don't know" books straight away.
- New local intent `reports_issue` with entity `site_issue`. What they say is kept as `site_issues` in the call record, in the Beat Plan comment summary, in the dashboard `slots`, and sent with `confirm_slot`.

## 0.18.0 (2026-11-27, SSE-15551)
From a call where the customer answered the planned-date question with "कोई और alternate slot है?" twice and the bot said "ठीक से सुनाई नहीं दिया" both times:
- New intent `asks_other_slots` ("koi aur slot hai?", "any other dates"). At the planned-date offer and at the time-window step it goes straight to `ALTERNATE_DAYS`, which reads out the real days. While other days are being offered it gives the next options, or ends with no slot when none are left.

## 0.17.0 (2026-11-26, SSE-15559)
Off-guard questions can be reviewed and taught:
- Every turn the bot could not handle (`deflected`, `not_understood`, `unclear`, `other_language`) is sent to the dashboard app with the customer's words and the step it happened at, and listed on the new Questions view.
- An admin can write the answer the bot should say, or teach an existing intent (for example `wants_human`, `do_not_call`) with that phrase, or ignore it.
- At the start of every call the bot reads the approved answers and taught phrases (`GET /api/knowledge`). New global intent `known_question` (with a `faq_id` entity) speaks an approved answer and re-asks the question; taught phrases are added to that intent's examples in the classifier prompt. An answer saved now is used from the next call.
- `GET /api/flow` on the bot also lists the intents, for the admin's "teach an intent" choice.

## 0.16.0 (2026-11-26, SSE-15557)
From a call where the customer asked the price three times and got "our team will tell you separately" each time:
- New global intent `asks_price` ("आप पैसे लोगे क्या?", "is it free"): the bot says "फ़िलहाल यह visit free है।" and asks the question again. Controlled by `policies.price_answer: free`; clear it to go back to the deflection. No amount is ever stated (`never_promise` now lists `price_amounts`, savings, generation, subsidy). NOTE: this relaxes the organisers' hard rule "never promises prices" for this one sentence and needs the owner's confirmation.
- "Our team will tell you separately" is said at most `policies.deflect_limit` (2) times in a row (off-topic questions, a "what does that mean?" with no explanation, or a price question when `price_answer` is cleared). The third time the bot says "इस बारे में मुझे अभी जानकारी नहीं है।" and a senior is asked to call back (reason `unanswered_question`).
- A plain "हाँ, बोलिए" at the greeting is the customer saying yes; the `household_member` examples now need a stated relationship, so family wording is no longer used on the customer himself.
- `price_or_offtopic` now covers generation, savings, subsidy and inverter questions only.

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
