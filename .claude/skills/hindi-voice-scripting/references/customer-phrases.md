# Customer phrase bank (extend from real recordings)

| Intent | What customers say | Agent handling |
|---|---|---|
| Busy now | "abhi busy hoon", "meeting mai hoon", "gaadi chala raha hoon", "baad mai call karo" | Ask one time window ("Shaam ko chalega?"), outcome `callback` |
| Ask family | "mere bete se baat karo", "ghar pe wife hai unse pooch lo", "ask my son" | Ask for a good time or number to call; do not take booking from an unknown person without confirming they are household; outcome `callback` with note |
| Not customer | "galat number hai", "kaun Sharma?", "aap kisko chahiye" | Apologise, outcome `wrong_number`, end |
| Don't call | "call mat karo", "number hata do", "DND pe hoon", "pareshan mat karo" | Apologise once, outcome `do_not_call`, end immediately |
| Already done | "abhi to saaf karwaya tha", "hum khud saaf kar lete hain" | Note it; ask if they still want the AMC visit (it doesn't count against plan only if business rules say so — don't promise) |
| Performance issue | "generation kam aa raha hai", "inverter pe error aa raha hai", "light nahi aa rahi" | Do not troubleshoot; offer human handover / service ticket, outcome `handed_over` |
| Price / charges | "kitna lagega?", "extra charge hai kya?", "free hai na?" | Never quote: "AMC plan mai jo included hai wahi hoga, charges ki detail team share karegi." Hand over if insistent |
| Vague time | "kabhi bhi aa jao", "jab aap free ho" | Offer earliest 2 slots |
| Confirmation | "haan", "ji", "theek hai", "chalega", "done", "ok ji", "haan haan bilkul" | Yes |
| Negation | "nahi", "na", "nahi chalega", "mushkil hai", "us din nahi ho payega" | No for that option; offer alternate |
| Unsure | "dekhta hoon", "pakka nahi pata", "confirm karke batata hoon" | Offer to hold a tentative slot? Prefer callback with time |
| Irritation | "kitni baar call karoge", "pehle bhi bola tha" | Apologise, be brief, skip non-essential steps, offer handover |
| Language switch | "English mai bolo", "Marathi chalega?" | Switch if supported, else apologise and offer human callback |
| Access issue | "society wale terrace nahi kholte", "chabi watchman ke paas hai" | Note in `notes`, remind to arrange access, don't block booking |
| Bill mention | "bill around 3 hazaar aata hai" | Not needed for cleaning; acknowledge, move on |
