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
**How to read these:** blue = the bot talks · amber = the system does something (Beat Plan / Zeus) · green = the call ends · grey dashed = continues in another part · red = can happen any time. After every call the bot runs: summarise_call, post_beatplan_comment.

### Overview

```mermaid
flowchart TD
  start["1. Start the call"]:::talk
  slot["2. Fix the date and time"]:::talk
  confirm["3. Check readiness and book"]:::talk
  hold["4. Pause the site"]:::talk
  later["5. Call back or hand over"]:::talk
  anytime["6. Can happen any time"]:::any
  start -->|"Yes"| slot
  slot -->|"Yes …"| confirm
  slot -->|"Wants exact time"| later
  confirm -->|"No"| hold
  hold -->|"Found one …"| confirm
  confirm -->|"Change day …"| slot
  anytime -.->|"Not available / site not ready"| hold
  anytime -.->|"Busy, call later"| later
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef system fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef terminal fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
  classDef jump fill:#F3F3F3,stroke:#999,color:#333,stroke-dasharray:4 3
  classDef any fill:#FDECEA,stroke:#C0504D,color:#5A1A18
```

### 1. Start the call

Check the customer can be called, say hello and make sure we're talking to the right person.

```mermaid
flowchart TD
  PRE_CALL[["Check before calling"]]:::system
  GREET("Say hello, check it's the customer"):::talk
  NOT_DIALLED(["Not called"]):::terminal
  PRE_CALL -->|"OK to call"| GREET
  PRE_CALL -->|"Can't call now"| NOT_DIALLED
  GREET -->|"Yes"| OUT_slot
  OUT_slot(["Go to part 2: Fix the date and time"]):::jump
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef system fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef terminal fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
  classDef jump fill:#F3F3F3,stroke:#999,color:#333,stroke-dasharray:4 3
  classDef any fill:#FDECEA,stroke:#C0504D,color:#5A1A18
```

### 2. Fix the date and time

Offer the planned date. If it doesn't suit, try another time that day or another day.

```mermaid
flowchart TD
  IN0(["From part 1 · From any step"]):::jump --> OFFER_PRIMARY
  IN1(["From part 3"]):::jump --> ALTERNATE_DAYS
  IN2(["From part 3"]):::jump --> TIME_BAND
  OFFER_PRIMARY("Offer the planned date"):::talk
  TIME_BAND("Tell the time window"):::talk
  CHECK_BAND[["Check another time that day"]]:::system
  ALTERNATE_DAYS("Offer other days"):::talk
  NO_SLOT_CLOSE(["No free slot"]):::terminal
  OFFER_PRIMARY -->|"Yes"| OUT_confirm
  OFFER_PRIMARY -->|"Asks the time"| TIME_BAND
  OFFER_PRIMARY -->|"No"| ALTERNATE_DAYS
  TIME_BAND -->|"Yes"| OUT_confirm
  TIME_BAND -->|"Wants exact time (first time)"| TIME_BAND
  TIME_BAND -->|"Wants exact time (asks again)"| OUT_later
  TIME_BAND -->|"Wants another time"| CHECK_BAND
  TIME_BAND -->|"No"| ALTERNATE_DAYS
  CHECK_BAND -->|"Free"| OUT_confirm
  CHECK_BAND -->|"Full"| ALTERNATE_DAYS
  ALTERNATE_DAYS -->|"No other days"| NO_SLOT_CLOSE
  ALTERNATE_DAYS -->|"Yes"| OUT_confirm
  ALTERNATE_DAYS -->|"Wants another time"| CHECK_BAND
  ALTERNATE_DAYS -->|"No (more days to offer)"| ALTERNATE_DAYS
  ALTERNATE_DAYS -->|"No (no more days)"| NO_SLOT_CLOSE
  OUT_confirm(["Go to part 3: Check readiness and book"]):::jump
  OUT_later(["Go to part 5: Call back or hand over"]):::jump
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef system fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef terminal fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
  classDef jump fill:#F3F3F3,stroke:#999,color:#333,stroke-dasharray:4 3
  classDef any fill:#FDECEA,stroke:#C0504D,color:#5A1A18
```

### 3. Check readiness and book

Make sure the ladder, water and light are ready, repeat the details and book the visit.

```mermaid
flowchart TD
  IN0(["From part 2"]):::jump --> READINESS_CHECK
  IN1(["From part 4"]):::jump --> RECAP
  IN2(["From part 4"]):::jump --> MANUAL_CONFIRM_CLOSE
  READINESS_CHECK("Ask: ladder, water, light ready?"):::talk
  RECAP("Repeat the details, get a yes"):::talk
  BOOK[["Book the visit"]]:::system
  CONFIRMED_CLOSE(["Visit booked"]):::terminal
  MANUAL_CONFIRM_CLOSE(["Team will confirm"]):::terminal
  READINESS_CHECK -->|"Yes"| RECAP
  READINESS_CHECK -->|"No"| OUT_hold
  RECAP -->|"Yes"| BOOK
  RECAP -->|"Change day"| OUT_slot
  RECAP -->|"Change time"| OUT_slot
  BOOK -->|"Done"| CONFIRMED_CLOSE
  BOOK -->|"System error"| MANUAL_CONFIRM_CLOSE
  OUT_hold(["Go to part 4: Pause the site"]):::jump
  OUT_slot(["Go to part 2: Fix the date and time"]):::jump
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef system fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef terminal fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
  classDef jump fill:#F3F3F3,stroke:#999,color:#333,stroke-dasharray:4 3
  classDef any fill:#FDECEA,stroke:#C0504D,color:#5A1A18
```

### 4. Pause the site

If the site isn't ready, get a date and the customer's OK to stop calls until then.

```mermaid
flowchart TD
  IN0(["From part 3"]):::jump --> ASK_READY_DATE
  IN1(["From any step"]):::jump --> SITE_ON_HOLD
  ASK_READY_DATE("Ask when it will be ready"):::talk
  FIND_SLOT_AFTER_READY[["Find a free day after that"]]:::system
  SITE_ON_HOLD("Ask when we can come"):::talk
  HOLD_CONSENT("Ask OK to pause calls"):::talk
  APPLY_HOLD[["Pause the site"]]:::system
  HOLD_CLOSE(["Site paused"]):::terminal
  HOLD_DECLINED_CLOSE(["Customer said no to pausing"]):::terminal
  ASK_READY_DATE -->|"Gives a date (if rebooking is on)"| FIND_SLOT_AFTER_READY
  ASK_READY_DATE -->|"Gives a date (default)"| HOLD_CONSENT
  FIND_SLOT_AFTER_READY -->|"Found one"| OUT_confirm
  FIND_SLOT_AFTER_READY -->|"None free"| HOLD_CONSENT
  SITE_ON_HOLD -->|"Gives a date"| HOLD_CONSENT
  HOLD_CONSENT -->|"Yes"| APPLY_HOLD
  HOLD_CONSENT -->|"No"| HOLD_DECLINED_CLOSE
  APPLY_HOLD -->|"Done"| HOLD_CLOSE
  APPLY_HOLD -->|"System error"| OUT_confirm
  OUT_confirm(["Go to part 3: Check readiness and book"]):::jump
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef system fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef terminal fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
  classDef jump fill:#F3F3F3,stroke:#999,color:#333,stroke-dasharray:4 3
  classDef any fill:#FDECEA,stroke:#C0504D,color:#5A1A18
```

### 5. Call back or hand over

The customer is busy or wants a person. Note when to call back, or ask a senior to call.

```mermaid
flowchart TD
  IN0(["From any step"]):::jump --> RECALL
  IN1(["From part 2 · From any step"]):::jump --> ESCALATE_RM
  RECALL("Ask when to call back"):::talk
  APPLY_RECALL[["Note the call-back time"]]:::system
  RECALL_CLOSE(["We'll call back"]):::terminal
  ESCALATE_RM[["Ask a senior to call"]]:::system
  RM_CLOSE(["Senior will call"]):::terminal
  RECALL -->|"Gives a time"| APPLY_RECALL
  APPLY_RECALL -->|"Done"| RECALL_CLOSE
  APPLY_RECALL -->|"System error"| RECALL_CLOSE
  ESCALATE_RM -->|"Done"| RM_CLOSE
  ESCALATE_RM -->|"System error"| RM_CLOSE
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef system fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef terminal fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
  classDef jump fill:#F3F3F3,stroke:#999,color:#333,stroke-dasharray:4 3
  classDef any fill:#FDECEA,stroke:#C0504D,color:#5A1A18
```

### 6. Can happen any time

Someone else answers, the customer asks us not to call, or nobody replies.

```mermaid
flowchart TD
  WRONG_PERSON("Someone else answered"):::talk
  WRONG_NUMBER_CLOSE(["Wrong number"]):::terminal
  DNC_CLOSE(["Won't call again"]):::terminal
  NO_ANSWER_CLOSE(["No answer"]):::terminal
  WRONG_PERSON -->|"Family member, can decide"| OUT_slot
  WRONG_PERSON -->|"Customer not home"| OUT_later
  WRONG_PERSON -->|"No"| WRONG_NUMBER_CLOSE
  ANY{{"Any step where the bot is talking"}}:::any
  ANY -.->|"Not available / site not ready"| OUT_hold
  ANY -.->|"Busy, call later"| OUT_later
  ANY -.->|"Wants a person"| OUT_later
  ANY -.->|"Don't call me"| DNC_CLOSE
  ANY -.->|"Someone else answered"| WRONG_PERSON
  ANY -.->|"No reply"| NO_ANSWER_CLOSE
  OUT_slot(["Go to part 2: Fix the date and time"]):::jump
  OUT_later(["Go to part 5: Call back or hand over"]):::jump
  OUT_hold(["Go to part 4: Pause the site"]):::jump
  classDef talk fill:#E8F0FE,stroke:#4A6FA5,color:#1B2A41
  classDef system fill:#FFF4E0,stroke:#C98A1B,color:#4A3200
  classDef terminal fill:#E6F4EA,stroke:#3C8D50,color:#1E4620
  classDef jump fill:#F3F3F3,stroke:#999,color:#333,stroke-dasharray:4 3
  classDef any fill:#FDECEA,stroke:#C0504D,color:#5A1A18
```

Handled in place at any step (the bot answers, then repeats its question):

- **Not clear** (`unclear`); after 2 in a row → Ask a senior to call
- **Asks price or something else** (`price_or_offtopic`)
<!-- flow-diagram:end -->
