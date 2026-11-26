# Service Scheduling call flow (Beat Plan driven)

Ticket: **SSE-15527** (Beat-Planing AI calling Epic; first tracked as TECH-56) · Flow version: see `flow.yaml`

Beat Plan already soft-allocates every site due in the next 15 days to a Service Hero and a tentative date, with backup dates. This bot calls the customer, turns that soft allocation into a **confirmed slot**, a **site on hold**, or an **escalation**, and writes a summary back to Beat Plan on every call.

## Running it

The Python bot runs this flow by default (`CALL_FLOW=service-scheduling` in `bot/.env`): `bot/agent/flow.py` executes the YAML, `bot/agent/beatplan.py` handles the actions (mocked from `app/data/bookings.json` until the Beat Plan / Zeus contracts land). `bot/tests/test_flow.py` runs every scenario below through the real engine. Text-mode try-out: `cd bot && uv run python simulate.py --booking BK-1001`.

## How this folder works

| File | What it holds | Who edits it |
|---|---|---|
| `flow.yaml` | States, transitions, guards, outcomes. The state machine code runs. | Product + eng |
| `intents.yaml` | What the LLM classifies customer turns into, with Hindi/English example phrases. Global intents work from any state. | Product, native speakers |
| `actions.yaml` | Beat Plan / Zeus calls the flow triggers, with status `live` / `contract_pending` / `stub`. | Eng |
| `policies.yaml` | Tunable rules: time bands, options per turn, exact-time limit, readiness items, hold policy, calling hours. | Product / ops |
| `prompts/hi.yaml`, `prompts/en.yaml` | What the bot says in each state. | Product, native speakers |
| `schemas/` | Beat Plan input (`context`) and the record posted back (`call-record`). | Eng |
| `scenarios.yaml` | Paper-test calls with the expected outcome. Add one per new branch. | Anyone |

**Principle:** the state machine lives in config, the LLM handles language. The LLM classifies intent, extracts dates/times, and speaks the line. It never decides to book: `confirm_slot` is reachable only from `RECAP → BOOK`, and `site_on_hold` only after explicit consent.

### Changing the flow (from Claude Code or by hand)

1. Edit the YAML (e.g. "add a state that asks for gate access before RECAP").
2. Add or update a scenario in `scenarios.yaml`.
3. Run the checker and redraw the diagram:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt   # first time only
   .venv/bin/python tools/flowcheck.py --write
   ```
4. Bump `version` in `flow.yaml`, add a line to `CHANGELOG.md`, open a PR.

The checker fails if a transition points at a missing state, an intent/event/flag/action is undeclared, a state is unreachable, an outcome can't be produced, a spoken state lacks a Hindi or English line, the two languages have different prompt keys, a readiness item has no question, or any scenario ends in the wrong outcome.

## The call, in words

1. **PRE_CALL**: load the Beat Plan context. Don't dial if the customer is DNC, it's outside calling hours, or the number is not on the test allowlist.
2. **GREET**: name, "calling from SolarSquare", recording disclosure, identity check. Family member → can continue; doesn't know the customer → wrong number.
3. **OFFER_PRIMARY** (the bot never mentions a time window unless the customer asks about time, so the team can still move the slot within the day): "Your maintenance is due, we have a slot on {date}. Will you be available?"
   - **Yes** → readiness check.
   - **Asks the time** → **TIME_BAND**: share the 3-hour time window (9–12, 12–3, 3–6). If they want an exact time, say we can't guarantee it but will try (and log their preferred time). If they insist again → **RM callback**. If they want a different band that day → check Beat Plan: free → readiness; full → alternate days.
   - **Not needed yet** ("plan is new", "panels are clean") → **NOT_NEEDED**: one short explanation (an early visit is a check-up), then ask again. Still no → `not_needed_now`.
   - **Suggests their own date** → if Beat Plan has that day, book it; otherwise **ASK_BAND_FOR_DATE** (morning / afternoon / evening) → `date_requested`: "the date isn't fixed yet, the team will confirm".
   - **Asks for a time outside 9 am to 6 pm** → say the service hours and repeat the question.
   - **Not that day** → **ALTERNATE_DAYS**: Beat Plan backup slots, closest first, 2 at a time. None fit → "no other slots now, team will reconnect".
4. **READINESS_CHECK**: A-type ladder, water source, light connection, one at a time. No ladder → first ask "can you arrange one before the visit?"; yes → carry on booking (noted as `will_arrange`). All yes → **RECAP** → **BOOK** (`confirm_slot`). Anything missing → ask by when it'll be ready → site on hold (or, if `on_missing_prereq: reschedule_after_ready_date`, first try a backup slot after that date).
5. **At any point**:
   - out of town / not serviceable → **SITE_ON_HOLD**: ask the date, then **explicit consent** ("you won't get calls from SolarSquare until then"). Yes → hold API. No → thank and close.
   - "call me later" → **RECALL**: ask a time range. The recall API doesn't exist yet (`stub`), so we post it as a Beat Plan comment and create a scheduler task.
   - asks for a person → **RM callback**. "Don't call me" → DNC. Price/technical questions → one-line deflection, stay in the step.
   - silence → no-answer close.
6. **POST_CALL** (every call, every outcome): LLM summary + structured record → Beat Plan comment.

## Emotions and voice

How good support agents adjust to the caller, and how the bot copies it. The LLM reads the customer's **mood** on every turn; `policies.yaml` (`moods`, `tones`) decides what changes. A state can force its own tone (`tone:` in `flow.yaml`).

| Mood | What you hear | What the bot does | Voice (pace / pitch / loudness) |
|---|---|---|---|
| Calm | Normal answers | Normal script, friendly | warm: 1.0 / 0 / 1.0 |
| Busy | "Jaldi bolo", one-word answers | Short form of each question, no intros; offers a call back if asked | brisk: 1.12 / 0 / 1.0 |
| Confused | "Samjha nahi", hesitates, elderly | Short, simple questions, one at a time; repeats key details | slow_clear: 0.85 / 0 / 1.1 |
| Annoyed | Sighs, "phir se call?" | Says "I understand" once before moving on; no pushing | empathetic: 0.88 / -0.1 / 0.95 |
| Angry | Raised voice, complaints | Same, and complaints stop the script: missed visit → apology + offer the date; anything else → support ticket + helpline | empathetic |
| Abusive | Insults | One calm line, supervisor call, end | calm_firm: 0.9 / -0.15 / 1.0 |

Rules the bot follows with upset customers, from de-escalation practice:
1. Acknowledge before anything else; never go straight back to the script question.
2. Own the specific problem ("nobody came last time") instead of a generic sorry.
3. Slow down and lower the voice; never speed up or get louder.
4. Give one concrete next step: a fixed date, a ticket, or the helpline (read slowly, digit by digit, and checked).
5. Apologise once; don't argue, don't promise timelines the bot can't keep.
6. If it's outside this call or turns abusive, hand over to a supervisor.

## Outcomes (exactly one per call)

| Outcome | Meaning |
|---|---|
| `slot_confirmed` | Primary Beat Plan slot confirmed |
| `slot_confirmed_alt_band` | Same date, different time window |
| `slot_confirmed_alt_day` | Backup date confirmed |
| `site_on_hold` | Customer consented to hold until a date |
| `hold_declined` | Not serviceable, but declined the hold |
| `no_slot_available` | No primary/backup slot worked |
| `rm_callback` | Supervisor call: insisted on exact time, asked for a person, off-script request or abuse |
| `recall_requested` | Asked to be called later (window captured) |
| `do_not_call` | Asked not to be called |
| `wrong_number` | Not the customer and doesn't know them |
| `no_answer` | Silence / voicemail |
| `needs_manual_confirmation` | Customer agreed but the Beat Plan/Zeus API failed |
| `not_dialled` | Blocked before dialling (DNC, hours, allowlist) |
| `not_needed_now` | Said the visit isn't needed yet, still no after one explanation |
| `date_requested` | Wanted their own date that Beat Plan doesn't have; request saved, team confirms |
| `support_ticket` | Upset customer: ticket raised, helpline given, didn't want to book now |
| `line_issue` | Customer couldn't hear the bot; call back |

## Product notes

- **Missing readiness ≠ always hold.** If the ladder will be ready by Thursday and a backup slot exists after Thursday, rebooking beats holding. Switch with `on_missing_prereq` in `policies.yaml`; default is `site_on_hold` per the current spec.
- **Exact-time requests are signal.** `preferred_exact_time` is logged even when we don't escalate, so Beat Plan can learn and route heroes.
- **Holds must expire back into Beat Plan** on `hold_until`, or they become silent churn.
- **North-star metric:** % of Beat Plan sites confirmed on the first call. Also track hold rate, RM escalation rate, no-slot rate, average handle time, voice latency.

## Open questions (owner: Beat Plan / Zeus team)

- API contracts for `fetch_beatplan_context`, `check_band_availability`, `get_backup_slots`, `confirm_slot`, `site_on_hold`, `create_rm_callback`, `post_beatplan_comment` (all `contract_pending` in `actions.yaml`).
- Does Beat Plan allocate bands or exact times today?
- Retry policy for no-answer, and allowed calling hours per city.
- Who owns the recall API, and when will it exist?

## Flow diagrams

**Shareable version:** [`flow.html`](flow.html) is the same flow as one self-contained page (open it in any browser, or send the file). Click a step to see what the bot says in Hindi and English, what the customer can answer, and which Beat Plan calls it makes. It is regenerated with these diagrams.

Generated from `flow.yaml` by `tools/flowcheck.py --write`; don't edit them by hand. The call is split into 6 sections (`sections` in `flow.yaml`). Step names come from each state's `title`, arrow text from `label` in `intents.yaml` and `labels` in `flow.yaml`, so renaming something there renames it here.

<!-- flow-diagram:start -->
**How to read this:** the call is one straight line of steps. Each step has a small table: what the customer says, and what the bot does next. Anything off the main line is a short detour, listed after it. Lines are shown in English with example values; the Hindi lines are in `prompts/hi.yaml`.

### The usual call

```mermaid
flowchart LR
  P0["Check before calling"]:::sys
  P1["1. Say hello, check it's the customer"]:::talk
  P2["2. Offer the planned date"]:::talk
  P3["3. Ask: ladder, water, light ready?"]:::talk
  P4["4. Repeat the details, get a yes"]:::talk
  P5["Book the visit"]:::sys
  P6(["Visit booked"]):::end_
  P0 --> P1 --> P2 --> P3 --> P4 --> P5 --> P6
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef sys fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef end_ fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
```

#### Step 1. Say hello, check it's the customer

> Hello, am I speaking with Rahul? This is Krupal from SolarSquare. This call is being recorded.

| Customer says | What happens |
|---|---|
| Yes | Step 2: Offer the planned date |

#### Step 2. Offer the planned date

> Rahul, your solar system maintenance is due. We have a slot on Saturday, 28 November. Will you be available that day?

| Customer says | What happens |
|---|---|
| Yes | Step 3: Ask: ladder, water, light ready? |
| Asks the time | Find another date or time: Tell the time window |
| No | Find another date or time: Offer other days |
| Suggests their own date (Beat Plan has that day) | Step 3: Ask: ladder, water, light ready? |
| Suggests their own date (time of day given too) | Call ends: Date request noted |
| Suggests their own date | Find another date or time: Ask the time for their date |

#### Step 3. Ask: ladder, water, light ready?

> Great. A few things need to be ready for the service, let me quickly confirm.

| Customer says | What happens |
|---|---|
| Yes | Step 4: Repeat the details, get a yes |
| No | Pause the site: Ask when it will be ready |
| Not needed here | Step 4: Repeat the details, get a yes |

#### Step 4. Repeat the details, get a yes

> So that's Saturday, 28 November, with the ladder, water and light ready. Shall I confirm it?

| Customer says | What happens |
|---|---|
| Yes | System: Book the visit |
| Change day | Find another date or time: Offer other days |
| Change time | Find another date or time: Tell the time window |

### Detours

#### Find another date or time

When the planned date or time doesn't suit, or the customer pushes back, find one that does.

**Apologise for the missed visit, offer the date**

> I'm really sorry nobody came last time; that shouldn't have happened. This time I'll make sure your visit happens. Would Saturday, 28 November work for you?

| Customer says | What happens |
|---|---|
| Yes | Step 3: Ask: ladder, water, light ready? |
| Asks the time | Tell the time window |
| No | Offer other days |

**Tell the time window**

> Our service hero will come between 8 am and 12 pm that day. Does that work for you?

| Customer says | What happens |
|---|---|
| Yes | Step 3: Ask: ladder, water, light ready? |
| Wants exact time (first time) | Bot answers and asks again |
| Wants exact time (asks again) | Call back or hand over: Arrange a supervisor call |
| Wants another time | Check another time that day |
| No | Offer other days |

**Check another time that day**

The bot doesn't speak here. **Free** → Step 3: Ask: ladder, water, light ready?; **Full** → Offer other days.

**Offer other days**

> No problem. Would Monday, 30 November, morning or Wednesday, 2 December, evening work for you?

| Customer says | What happens |
|---|---|
| No other days | Call ends: No free slot |
| Yes | Step 3: Ask: ladder, water, light ready? |
| Wants another time | Check another time that day |
| Suggests their own date (Beat Plan has that day) | Step 3: Ask: ladder, water, light ready? |
| Suggests their own date (time of day given too) | Call ends: Date request noted |
| Suggests their own date | Ask the time for their date |
| No (more days to offer) | Bot answers and asks again |
| No (no more days) | Call ends: No free slot |

**Explain why the visit helps now**

> You're right, the plan is new. That's why the first visit works as a check-up, so any problem is caught early. Shall I book Saturday, 28 November for you?

| Customer says | What happens |
|---|---|
| Yes | Step 3: Ask: ladder, water, light ready? |
| Asks the time | Tell the time window |
| No | Call ends: Not needed yet, call later |

**Ask the time for their date**

> Sure. On Thursday, 10 December, would morning, afternoon or evening suit you?

| Customer says | What happens |
|---|---|
| Picks a time of day | Call ends: Date request noted |

#### Pause the site

If the site isn't ready, get a date and the customer's OK to stop calls until then.

**Ask when it will be ready**

> Understood. By when can the A-type ladder be arranged? Could you give me a date?

| Customer says | What happens |
|---|---|
| Gives a date (if rebooking is on) | Find a free day after that |
| Gives a date (default) | Ask OK to pause calls |

**Find a free day after that**

The bot doesn't speak here. **Found one** → Step 4: Repeat the details, get a yes; **None free** → Ask OK to pause calls.

**Ask when we can come**

> Understood. From which date will your site be ready for service?

| Customer says | What happens |
|---|---|
| Gives a date | Ask OK to pause calls |

**Ask OK to pause calls**

> Okay, we'll put your site on hold until 5 December. Until then you won't get service calls from SolarSquare. Is that okay?

| Customer says | What happens |
|---|---|
| Yes | Pause the site |
| No | Call ends: Customer said no to pausing |

**Pause the site**

The bot doesn't speak here. **Done** → Call ends: Site paused; **System error** → Call ends: Team will confirm.

#### Call back or hand over

The customer is busy or wants a person. Note when to call back, or ask a senior to call.

**Ask when to call back**

> Of course. What time would be good to call you back?

| Customer says | What happens |
|---|---|
| Gives a time | Note the call-back time |

**Note the call-back time**

The bot doesn't speak here. **Done** → Call ends: We'll call back; **System error** → Call ends: We'll call back.

**Arrange a supervisor call**

The bot doesn't speak here. **Done** → Call ends: Supervisor will call; **System error** → Call ends: Supervisor will call.

**Apologise, raise a ticket, give the helpline**

> I understand, you've had trouble, and I'm sorry. I've raised a support ticket for you, and our support team will contact you soon. You can also call our helpline, so please take a pen. The number is: nine eight three zero zero, zero three zero zero zero. Have you noted it down?

| Customer says | What happens |
|---|---|
| Yes | Offer to fix the visit meanwhile |
| No | Offer to fix the visit meanwhile |

**Offer to fix the visit meanwhile**

> Thank you. Meanwhile, shall I fix your maintenance visit?

| Customer says | What happens |
|---|---|
| Yes | Step 2: Offer the planned date |
| No | Call ends: Support ticket raised |

### Any time in the call

| Customer says | What happens |
|---|---|
| Not available / site not ready | Ask when we can come |
| Busy, call later | Ask when to call back |
| Wants a person | Arrange a supervisor call |
| Don't call me | Won't call again |
| Someone else answered | Someone else answered |
| Nobody came last time | Apologise for the missed visit, offer the date |
| Upset or other complaint | Apologise, raise a ticket, give the helpline |
| Something outside this call | Arrange a supervisor call |
| Abusive | Arrange a supervisor call |
| Can't hear the bot | Bot asks again; after 1 times in a row: Can't hear, call back |
| Wants another language | Bot answers briefly, then repeats its question |
| Asks to wait | Bot answers briefly, then repeats its question |
| Mentions a problem at the site | Bot answers briefly, then repeats its question |
| Asks who is calling | Bot answers briefly, then repeats its question |
| Asks if it's a robot | Bot answers briefly, then repeats its question |
| Asks what the visit involves | Bot answers briefly, then repeats its question |
| Asks if someone must be home | Bot answers briefly, then repeats its question |
| Not needed yet | Explain why the visit helps now |
| Wants a time outside 9 am to 6 pm | Bot answers briefly, then repeats its question |
| Not clear | Bot asks again; after 2 times in a row: Arrange a supervisor call |
| Asks to repeat | Bot answers briefly, then repeats its question |
| Asks about price | Bot answers briefly, then repeats its question |
| No reply | No answer |

**Someone else answered**

> No problem. Am I speaking with someone from Rahul's home?

| Customer says | What happens |
|---|---|
| Family member, can decide | Step 2: Offer the planned date |
| Customer not home | Call back or hand over: Ask when to call back |
| No | Call ends: Wrong number |


### How a call can end

| Ending | Outcome code |
|---|---|
| Visit booked | `slot_confirmed / slot_confirmed_alt_band / slot_confirmed_alt_day` |
| Site paused | `site_on_hold` |
| Customer said no to pausing | `hold_declined` |
| No free slot | `no_slot_available` |
| Supervisor will call | `rm_callback` |
| We'll call back | `recall_requested` |
| Won't call again | `do_not_call` |
| Wrong number | `wrong_number` |
| No answer | `no_answer` |
| Team will confirm | `needs_manual_confirmation` |
| Not needed yet, call later | `not_needed_now` |
| Date request noted | `date_requested` |
| Support ticket raised | `support_ticket` |
| Can't hear, call back | `line_issue` |
| Not called | `not_dialled` |

After every call, whatever the ending, the system runs: summarise_call, post_beatplan_comment.
<!-- flow-diagram:end -->
