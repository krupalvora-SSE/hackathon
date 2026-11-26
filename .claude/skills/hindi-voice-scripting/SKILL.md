---
name: hindi-voice-scripting
description: Writing system prompts and spoken lines for a Hindi/Hinglish phone agent — tone, code-mixing, numbers, dates, time slots, TTS-friendly text, and understanding how customers actually speak. Use when writing or editing prompts, agent utterances, cached audio lines, or when STT/TTS mishandles Hindi.
---

# Hindi / Hinglish Voice Scripting

## Voice persona
- Name: pick a teammate whose voice is cloned (e.g. "Pooja from SolarSquare service team"). Persona should match the cloned voice's gender in Hindi verb forms (`karti hoon` vs `karta hoon`). Set this once in the prompt and never mix.
- Register: polite urban Hinglish, the way a good SolarSquare service caller talks. Use `aap`, `ji`. Not shuddh Hindi ("sampark", "uplabdh", "dinank" sound robotic). Use English for what people actually say in English: solar, panel, cleaning, technician, slot, booking, address, plan, SMS.
- Length: 1–2 short sentences per turn, max ~20 words. One question per turn.
- Mirror the customer: if they speak mostly English, switch to English; if Marathi-mixed, stay Hindi but simple.

## System prompt skeleton (keep it under ~1.5k tokens)
```
You are Pooja, a service coordinator at SolarSquare, calling customers about
their solar panel cleaning under the AMC plan. Speak natural Hinglish as an
Indian service caller would, female verb forms. Output is spoken over a phone,
so: no lists, no emojis, no markdown, max 2 short sentences, one question at a time.
Write Hindi words in Devanagari and English words (solar, panel, slot) in
Devanagari transliteration. Write all numbers, dates and times as words.

Never promise prices, savings, generation numbers or subsidy amounts. If asked,
say the team will share details. If the customer asks to stop or not be called,
apologise once and end the call with outcome do_not_call.

Today: {today_hindi} ({today_iso}). Current step: {state}.
Customer: {context_block}
Next 10 days with availability: {calendar_block}
```
Re-check the TTS: some vendors pronounce Devanagari Hindi better, others do better with Roman Hinglish. Test both and pick one script for all output.

## Numbers, dates, times (where most bugs live)

**Output** (agent → TTS): spell out in words, never digits.
- "3 Dec 7-9 AM" → "तीन दिसंबर, बुधवार, सुबह सात से नौ बजे के बीच"
- Avoid "07:00". Avoid AM/PM; say `सुबह` (morning), `दोपहर` (afternoon), `शाम` (evening).
- Address numbers: read flat/house numbers digit by digit if long ("बी, चार सौ बारह" is ok; pincode "चार एक एक शून्य चार पाँच").

**Input** (customer → STT → LLM): do date resolution in code or with a precomputed calendar, never let the LLM do calendar math from scratch.
Put a 10-day table in the prompt:
```
आज = बुधवार 26 नवंबर | कल = गुरुवार 27 नवंबर | परसों = शुक्रवार 28 नवंबर
अगला सोमवार = 1 दिसंबर (slots: सुबह 7-9 ✓, शाम 4-6 ✓) ...
```
Expressions to handle:
| Customer says | Meaning |
|---|---|
| kal / parso | tomorrow / day after (note: `kal` also means yesterday — context is future here) |
| agle hafte, next week | next Mon–Sun |
| is Sunday / aane wala Sunday | upcoming Sunday |
| pandrah tareekh, 15 ko | 15th of this month, or next month if passed |
| subah subah / jaldi | earliest morning slot |
| shaam ko, office ke baad | evening slot |
| dopahar mai | afternoon — offer morning/evening instead, explain briefly |
| koi bhi din, jab aap bolo | offer earliest 2 slots |
| Diwali ke baad, shaadi ke baad | callback or ask for a specific date |

Hindi numerals customers say: ek do teen chaar paanch chhe saat aath nau das, gyarah barah ... pandrah, bees, pachees, tees; `saadhe` (+.5), `paune` (−.25), `sava` (+.25): "saadhe aath baje" = 8:30.

## Acknowledgement and filler lines (pre-render these as audio)
- `जी, बिल्कुल।` `अच्छा, ठीक है।` `समझ गई।`
- While a tool runs: `एक सेकंड, मैं स्लॉट देख रही हूँ।`
- Didn't catch: `माफ़ कीजिए, आवाज़ थोड़ी कट गई, फिर से बताएँगे?`
- Recording disclosure: `यह कॉल क्वालिटी के लिए रिकॉर्ड की जा रही है।`

## Example opening (Flow A)
> नमस्ते, क्या मेरी बात शर्मा जी से हो रही है? ... मैं पूजा बोल रही हूँ, SolarSquare सर्विस टीम से। यह कॉल रिकॉर्ड की जा रही है। आपके AMC प्लान में सोलर पैनल की अगली क्लीनिंग ड्यू है, क्या दो मिनट बात कर सकते हैं?

Split this into two turns if the customer starts talking: greeting + identity first, purpose after they confirm.

## Things customers say that the agent must understand
See `references/customer-phrases.md` for a phrase bank (busy, ask my son, already cleaned, panels not working, rude/angry, price questions). Extend it from the real call recordings: listen to 10–15 calls and add every phrase the bot would currently misunderstand. This is where the team's native speaker adds the most value.

## STT tips
- Pass a keyword/hotword list if the vendor supports it: SolarSquare, AMC, cleaning, technician, landmark, society, terrace, the customer's name, nearby area names from the address.
- Expect transliteration variety in transcripts ("kal"/"कल"/"call"). Prompt the LLM that transcripts are noisy phone ASR and to ask again rather than guess on dates and times.
