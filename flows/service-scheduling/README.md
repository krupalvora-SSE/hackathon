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
3. **OFFER_PRIMARY**: "Your maintenance is due, we have a slot on {date}. Will you be available?"
   - **Yes** → readiness check.
   - **Asks the time** → **TIME_BAND**: share the 4-hour band. If they want an exact time, say we can't guarantee it but will try (and log their preferred time). If they insist again → **RM callback**. If they want a different band that day → check Beat Plan: free → readiness; full → alternate days.
   - **Not that day** → **ALTERNATE_DAYS**: Beat Plan backup slots, closest first, 2 at a time. None fit → "no other slots now, team will reconnect".
4. **READINESS_CHECK**: A-type ladder, water source, light connection, one at a time. All yes → **RECAP** → **BOOK** (`confirm_slot`). Anything missing → ask by when it'll be ready → site on hold (or, if `on_missing_prereq: reschedule_after_ready_date`, first try a backup slot after that date).
5. **At any point**:
   - out of town / not serviceable → **SITE_ON_HOLD**: ask the date, then **explicit consent** ("you won't get calls from SolarSquare until then"). Yes → hold API. No → thank and close.
   - "call me later" → **RECALL**: ask a time range. The recall API doesn't exist yet (`stub`), so we post it as a Beat Plan comment and create a scheduler task.
   - asks for a person → **RM callback**. "Don't call me" → DNC. Price/technical questions → one-line deflection, stay in the step.
   - silence → no-answer close.
6. **POST_CALL** (every call, every outcome): LLM summary + structured record → Beat Plan comment.

## Outcomes (exactly one per call)

| Outcome | Meaning |
|---|---|
| `slot_confirmed` | Primary Beat Plan slot confirmed |
| `slot_confirmed_alt_band` | Same date, different 4-hour band |
| `slot_confirmed_alt_day` | Backup date confirmed |
| `site_on_hold` | Customer consented to hold until a date |
| `hold_declined` | Not serviceable, but declined the hold |
| `no_slot_available` | No primary/backup slot worked |
| `rm_callback` | Insisted on exact time, or asked for a person |
| `recall_requested` | Asked to be called later (window captured) |
| `do_not_call` | Asked not to be called |
| `wrong_number` | Not the customer and doesn't know them |
| `no_answer` | Silence / voicemail |
| `needs_manual_confirmation` | Customer agreed but the Beat Plan/Zeus API failed |
| `not_dialled` | Blocked before dialling (DNC, hours, allowlist) |

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
  P5["5. Ask about any issue on the site"]:::talk
  P6["6. Ask which issue"]:::talk
  P7["Book the visit"]:::sys
  P8(["Visit booked"]):::end_
  P0 --> P1 --> P2 --> P3 --> P4 --> P5 --> P6 --> P7 --> P8
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef sys fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef end_ fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
```

#### Step 1. Say hello, check it's the customer

> Hello, is this Rahul? This is Krupal from SolarSquare. This call is being recorded.

| Customer says | What happens |
|---|---|
| Yes | Step 2: Offer the planned date |
| No | Someone else answered |
| Family member, can decide | Step 2: Offer the planned date |

#### Step 2. Offer the planned date

> Rahul, your solar system maintenance is due. We have a slot on Saturday, 28 November, between 8 am and 12 pm. Will you be available that day?

| Customer says | What happens |
|---|---|
| Yes | Step 3: Ask: ladder, water, light ready? |
| Asks the time | Find another date or time: Tell the time window |
| Wants exact time | Find another date or time: Tell the time window |
| Wants another time | Find another date or time: Check another time that day |
| Asks for other slots | Find another date or time: Offer other days |
| No | Find another date or time: Offer other days |
| Suggests their own date (Beat Plan has that day) | Step 3: Ask: ladder, water, light ready? |
| Suggests their own date (not in Beat Plan) | Find another date or time: Ask the time for their date |

#### Step 3. Ask: ladder, water, light ready?

> Great. A few things need to be ready for the service, let me quickly confirm.

| Customer says | What happens |
|---|---|
| Yes | Step 4: Repeat the details, get a yes |
| No | Pause the site: Ask when it will be ready |
| Maybe / not sure (to a yes-no question) | Bot answers and asks again |
| Can't say / doesn't know | Bot answers and asks again |

#### Step 4. Repeat the details, get a yes

> So that's Saturday, 28 November. {ready_note}. Shall I confirm it?

| Customer says | What happens |
|---|---|
| Yes | Step 5: Ask about any issue on the site |
| No | Check readiness and book: Ask what to change |
| Corrects an earlier ladder / water / light answer | Step 3: Ask: ladder, water, light ready? |
| Change day | Find another date or time: Offer other days |
| Change time | Find another date or time: Tell the time window |

#### Step 5. Ask about any issue on the site

> Alright. One last thing: is there any problem on your site you would like us to know about, like rusting or damage?

| Customer says | What happens |
|---|---|
| Reports a site issue | System: Book the visit |
| Yes | Step 6: Ask which issue |
| No | System: Book the visit |
| Can't say / doesn't know | System: Book the visit |

#### Step 6. Ask which issue

> Which problem is it, could you tell me?

| Customer says | What happens |
|---|---|
| Reports a site issue | System: Book the visit |
| Yes | System: Book the visit |
| No | System: Book the visit |
| Can't say / doesn't know | System: Book the visit |

### Detours

#### Find another date or time

When the planned date or time doesn't suit, or the customer pushes back, find one that does.

**Tell the time window**

> Our service hero will come between 8 am and 12 pm that day. Does that work for you?

| Customer says | What happens |
|---|---|
| Yes (already checked) | Step 4: Repeat the details, get a yes |
| Yes | Step 3: Ask: ladder, water, light ready? |
| Wants exact time (first time) | Bot answers and asks again |
| Wants exact time (asks again) | Call back or hand over: Ask a senior to call |
| Wants another time | Check another time that day |
| Asks for other slots | Offer other days |
| No | Offer other days |

**Check another time that day**

The bot doesn't speak here. **Free (already checked)** → Step 4: Repeat the details, get a yes; **Free** → Step 3: Ask: ladder, water, light ready?; **Full** → Offer other days.

**Offer other days**

> No problem. Would Monday, 30 November, morning or Wednesday, 2 December, evening work for you?

| Customer says | What happens |
|---|---|
| No other days | Call ends: No free slot |
| Yes (already checked) | Step 4: Repeat the details, get a yes |
| Yes | Step 3: Ask: ladder, water, light ready? |
| Wants another time | Check another time that day |
| Suggests their own date (not in Beat Plan) | Ask the time for their date |
| Suggests their own date (already checked) | Step 4: Repeat the details, get a yes |
| Suggests their own date | Step 3: Ask: ladder, water, light ready? |
| Asks for other slots (more days to offer) | Bot answers and asks again |
| Asks for other slots (no more days) | Call ends: No free slot |
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
| Can't say / doesn't know | Call ends: Date request noted |

#### Check readiness and book

Make sure the ladder, water and light are ready, repeat the details, ask about any issue on the site and book the visit.

**Ask what to change**

> Okay, what would you like to change, the day or the time?

| Customer says | What happens |
|---|---|
| Corrects an earlier ladder / water / light answer | Step 3: Ask: ladder, water, light ready? |
| Change day | Find another date or time: Offer other days |
| Change time | Find another date or time: Tell the time window |
| Wants another time | Find another date or time: Check another time that day |
| Suggests their own date (not in Beat Plan) | Find another date or time: Ask the time for their date |
| Suggests their own date | Step 4: Repeat the details, get a yes |

#### Pause the site

If the site isn't ready, get a date and the customer's OK to stop calls until then.

**Ask when it will be ready**

> Understood. By when can the A-type ladder be arranged? Could you give me a date?

| Customer says | What happens |
|---|---|
| Gives a date (if rebooking is on) | Find a free day after that |
| Gives a date (default) | Ask OK to pause calls |
| Can't say / doesn't know | Call back or hand over: Ask a senior to call |

**Find a free day after that**

The bot doesn't speak here. **Found one** → Step 4: Repeat the details, get a yes; **None free** → Ask OK to pause calls.

**Ask when we can come**

> Understood. From which date will your site be ready for service?

| Customer says | What happens |
|---|---|
| Gives a date | Ask OK to pause calls |
| Can't say / doesn't know | Call back or hand over: Ask a senior to call |

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
| Can't say / doesn't know | Ask a senior to call |

**Note the call-back time**

The bot doesn't speak here. **Done** → Call ends: We'll call back; **System error** → Call ends: Team will confirm.

**Ask a senior to call**

The bot doesn't speak here. **Done** → Call ends: Senior will call; **System error** → Call ends: Team will confirm.

### Any time in the call

| Customer says | What happens |
|---|---|
| Not available / site not ready | Ask when we can come |
| Busy, call later | Ask when to call back |
| Wants a person | Ask a senior to call |
| Don't call me | Won't call again |
| Someone else answered / not the person asked for | Someone else answered |
| Says it is the wrong number | Wrong number |
| Not needed yet | Explain why the visit helps now |
| Wants a time outside 9 am to 6 pm | Bot answers briefly, then repeats its question |
| Not clear | Bot asks again; after 2 times in a row: Ask a senior to call |
| Asks to repeat | Bot answers briefly, then repeats its question |
| Asks us to hold on | Bot answers briefly, then repeats its question |
| Asks who is calling | Bot answers briefly, then repeats its question |
| Asks where we got the number | Bot answers briefly, then repeats its question |
| Asks if it is a machine | Bot answers briefly, then repeats its question |
| Asks the time | Bot answers briefly, then repeats its question |
| Death, accident or serious emergency in the family | Kind goodbye, calls paused |
| Upset with how the bot is behaving | Kind goodbye, calls paused |
| Can't take the visit for a personal reason (ill, family matter) | Ask when to call back |
| Complains about earlier service | Ask a senior to call |
| Speaks a language other than Hindi or English | Bot asks again; after 2 times in a row: Ask a senior to call |
| Chit-chat that is not an answer | Bot answers briefly, then repeats its question |
| Asks what something means or why it is needed | Bot answers briefly, then repeats its question |
| Asks if the visit costs anything | Bot answers briefly, then repeats its question |
| Asks something an admin has approved an answer for | Bot answers briefly, then repeats its question |
| Asks something we can't answer (generation, inverter, savings...) | Bot answers briefly, then repeats its question |
| No reply | No answer |

**Someone else answered**

> No problem. Am I speaking with someone from Rahul's home?

| Customer says | What happens |
|---|---|
| Someone else answered / not the person asked for | Call ends: Wrong number |
| Yes | Step 2: Offer the planned date |
| Family member, can decide | Step 2: Offer the planned date |
| Customer not home | Call back or hand over: Ask when to call back |
| No | Call ends: Wrong number |


### How a call can end

| Ending | Outcome code |
|---|---|
| Visit booked | `slot_confirmed / slot_confirmed_alt_band / slot_confirmed_alt_day` |
| Kind goodbye, calls paused | `site_on_hold` |
| Site paused | `site_on_hold` |
| Customer said no to pausing | `hold_declined` |
| No free slot | `no_slot_available` |
| Senior will call | `rm_callback` |
| We'll call back | `recall_requested` |
| Won't call again | `do_not_call` |
| Wrong number | `wrong_number` |
| No answer | `no_answer` |
| Team will confirm | `needs_manual_confirmation` |
| Not needed yet, call later | `not_needed_now` |
| Date request noted | `date_requested` |
| Not called | `not_dialled` |

After every call, whatever the ending, the system runs: summarise_call, post_beatplan_comment.
<!-- flow-diagram:end -->
