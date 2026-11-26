# Starter scenario suite

| ID | Flow | Situation | Expected outcome |
|---|---|---|---|
| S01 | schedule | Happy path, picks first offered slot | scheduled |
| S02 | schedule | Asks for afternoon; accepts evening instead | scheduled (16:00-18:00) |
| S03 | schedule | "Kabhi bhi aa jao" | scheduled (earliest) |
| S04 | schedule | Wants a full day, accepts alternative | scheduled (alt) |
| S05 | schedule | Corrects landmark during address read-back | scheduled + landmark updated |
| S06 | schedule | "Abhi gaadi chala raha hoon, shaam ko call karo" | callback (evening) |
| S07 | schedule | "Mere bete se poochna padega", later agrees | scheduled or callback |
| S08 | schedule | Wife picks up, husband is customer, she books | scheduled, spoke_to=family_member |
| S09 | schedule | Wrong number | wrong_number |
| S10 | schedule | "Call mat karo dobara" | do_not_call, dnc set |
| S11 | schedule | Asks "kitna charge lagega?" twice | no price stated; scheduled or handed_over |
| S12 | schedule | Says generation dropped, inverter error | handed_over |
| S13 | schedule | Speaks mostly English | scheduled, agent switches to English |
| S14 | schedule | Long pauses (5s) between answers | scheduled, no premature end |
| S15 | schedule | Total silence after pickup | no_answer |
| S16 | confirm | Confirms booking | confirmed |
| S17 | confirm | "Kal nahi, parso shaam ko" | rescheduled (new slot correct date) |
| S18 | confirm | Cancels: "ghar pe koi nahi hoga, bahar ja rahe hain", declines reschedule | cancelled with reason |
| S19 | confirm | Interrupts mid-sentence repeatedly | confirmed, no repeated monologues |
| S20 | confirm | Heavy traffic noise, mumbled date | agent re-asks, correct outcome |
| S21 | confirm | "Are you a robot?" | honest answer, continues or handed_over |
| S22 | confirm | Angry: "teen baar call kar chuke ho" | apology, brief, confirmed or handed_over |
| S23 | confirm | Society terrace locked, keys with watchman | confirmed + note |
| S24 | any | Asks for a human | handed_over |
