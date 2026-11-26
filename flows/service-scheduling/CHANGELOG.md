# Changelog: service-scheduling flow

Bump `version` in `flow.yaml` with every change and add a line here. The version is stamped on every call record (`flow_version`), so results can be compared across flow changes.

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
