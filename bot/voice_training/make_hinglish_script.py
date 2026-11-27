"""Build a ~30 minute Hinglish recording script for training our own Piper voice.

    uv run python voice_training/make_hinglish_script.py            # -> voice_training/script_hinglish.txt

Hinglish, as the bot actually talks: Hindi in Devanagari with English words where callers use them
("आपके solar system का maintenance due है"). Two parts:

1. Every line the live call can speak (flows/service-scheduling/prompts/hi.yaml), rendered by the flow's
   own renderer with varied names, weekdays, dates, time bands, missing items and hold dates.
2. Extra call lines: out-of-scope answers, identity questions, empathy, numbers and dates, and general
   Hinglish sentences, so the voice also covers sounds the prompts don't.

Lines are timed with the stock Piper voice (a proxy for reading speed) until the target length.
Training text is what Piper gets at call time: prepare.py writes to_devanagari(line) to metadata.csv.
"""
from __future__ import annotations

import argparse
import itertools
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

NAMES = ["Rahul", "Amit", "Priya", "Sunita", "Rajesh", "Neha", "Vikram", "Anjali", "Suresh", "Kavita", "Manoj",
         "Pooja", "Arjun", "Meena", "Deepak", "Sneha", "Ramesh", "Asha", "Nitin", "Geeta", "Sanjay", "Rekha"]
BANDS = ["morning", "afternoon", "evening"]
ITEMS = [["ladder"], ["water"], ["light"], ["ladder", "water"], ["water", "light"]]

EXTRA = """
जी, मैं समझ सकता हूँ। Price के बारे में आपको हमारी team अलग से details भेज देगी।
माफ़ कीजिए, इस बारे में मुझे ज़्यादा जानकारी नहीं है, पर मैं आपकी request note कर लेता हूँ।
आपकी complaint के लिए मैं senior team को बोल देता हूँ, वो आपको callback करेंगे।
Generation कम आ रहा है तो cleaning के बाद अक्सर units बढ़ जाते हैं।
Panels पर dust जमने से generation थोड़ा कम हो जाता है, इसलिए regular cleaning ज़रूरी है।
यह service आपके AMC contract में शामिल है, इसके लिए कोई extra charge नहीं है।
Payment या bill से जुड़े सवाल के लिए customer care आपको अलग से call करेगी।
जी नहीं, मैं robot नहीं हूँ, मैं SolarSquare की तरफ़ से AI assistant बोल रहा हूँ।
मैं SolarSquare का assistant हूँ और आपकी solar cleaning visit schedule करने के लिए call किया है।
आपका number हमारे records में आपकी solar site के लिए registered है।
अगर आप busy हैं तो मैं थोड़ी देर बाद call कर सकता हूँ, कब call करूँ?
ठीक है जी, मैं शाम को पाँच बजे के बाद दोबारा call कर लूँगा।
कोई बात नहीं, आप आराम से बताइए, मैं line पर हूँ।
जी, एक minute, मैं आपका schedule check कर लेता हूँ।
बस दो minute लगेंगे, फिर मैं आपका slot confirm कर दूँगा।
माफ़ कीजिए, आवाज़ थोड़ी कट रही है, क्या आप एक बार फिर बोल सकते हैं?
Sorry, मैं आपकी बात ठीक से सुन नहीं पाया, please दोबारा बताइए।
अच्छा, तो आप कह रहे हैं कि अगले हफ़्ते का कोई दिन ठीक रहेगा?
अगले महीने की पहली तारीख के बाद कभी भी चलेगा, ठीक है, मैं note कर लेता हूँ।
घर पर कोई ना कोई तो रहेगा ना उस दिन? बस छत तक जाने का रास्ता खुला होना चाहिए।
Society में watchman को बता दीजिएगा कि हमारे service hero आने वाले हैं।
Terrace की key किसके पास रहती है? Hero को access मिल जाएगा ना?
Building के roof तक जाने के लिए सीढ़ी है या lift से जाना पड़ता है?
पानी का नल panels के पास है तो cleaning जल्दी हो जाती है।
अगर पानी नहीं है तो एक बाल्टी पानी भी चल जाएगा।
Light का connection इसलिए चाहिए क्योंकि cleaning machine power से चलती है।
मुझे बहुत दुख हुआ यह सुनकर, आप अपना ध्यान रखिए, हम बाद में बात कर लेंगे।
कोई जल्दी नहीं है जी, जब सब ठीक हो जाए तब हम visit plan कर लेंगे।
Emergency है तो आप बिल्कुल चिंता मत कीजिए, मैं यह call यहीं खत्म करता हूँ।
आपकी family में function है तो उस हफ़्ते के बाद का कोई दिन बता दीजिए।
आप out of station हैं तो वापस आने के बाद की date बता दीजिए।
बारिश के मौसम में हम दो-तीन दिन आगे भी कर सकते हैं, आप tension मत लीजिए।
Weekend पर slot थोड़ा मुश्किल से मिलता है, weekday चलेगा क्या?
सोमवार से शनिवार तक सुबह नौ बजे से शाम छह बजे तक visit हो सकती है।
रविवार को हमारी team छुट्टी पर रहती है, तो रविवार का slot नहीं मिलेगा।
सुबह नौ से बारह, दोपहर बारह से तीन, या शाम तीन से छह, कौन सा time ठीक रहेगा?
Exact time तो मैं नहीं बता सकता, पर hero आने से पहले आपको call कर देंगे।
Visit से एक दिन पहले आपको reminder SMS भी आ जाएगा।
आपकी booking confirm हो गई है, details आपको WhatsApp पर भी मिल जाएँगी।
आपका slot cancel करना है या किसी और दिन reschedule करना है?
ठीक है, मैंने आपका slot cancel कर दिया है, जब चाहें दोबारा book कर लीजिए।
आपने बताया कि ladder नहीं है, तो क्या visit से पहले arrange हो पाएगा?
A-type ladder मतलब वो folding सीढ़ी जो अपने आप खड़ी हो जाती है।
आपकी site को हम पंद्रह दिन के लिए hold पर रख देते हैं, फिर दोबारा call करेंगे।
Hold के दौरान आपको SolarSquare से इस बारे में कोई call नहीं आएगी।
मैं आपकी बात senior manager तक पहुँचा देता हूँ, वो खुद आपसे बात करेंगे।
हमारे engineer ने पिछली visit में जो note लिखा था, वो मैं team को भेज देता हूँ।
आपका inverter या meter में कोई problem है तो वो अलग team देखती है।
Warranty के सवालों के लिए support team से आपको message आ जाएगा।
Thank you जी, आपसे बात करके अच्छा लगा, आपका दिन शुभ हो।
धन्यवाद, SolarSquare चुनने के लिए, कोई भी सवाल हो तो बेझिझक call कीजिए।
नमस्ते, मैं SolarSquare से बोल रहा हूँ, क्या अभी दो minute बात हो सकती है?
Hello? Hello, क्या आपको मेरी आवाज़ आ रही है?
जी हाँ, मैं सुन रहा हूँ, आप बोलिए।
अच्छा अच्छा, समझ गया, कोई बात नहीं।
बिल्कुल, यह तो बहुत अच्छी बात है।
हम्म, ठीक है, तो फिर ऐसा करते हैं।
जी, सही बात है आपकी।
Perfect, तो यह पक्का रहा।
Great, मैं अभी confirm कर देता हूँ।
अरे वाह, फिर तो कोई problem ही नहीं है।
Okay, कोई बात नहीं, हम दूसरा option देख लेते हैं।
क्या आप मुझे अपनी पसंद का कोई दिन बता सकते हैं?
आपको सुबह का time ठीक रहता है या शाम का?
और क्या उस दिन घर पर कोई बड़ा रहेगा?
माफ़ कीजिए, मैं सिर्फ़ हिंदी और English में बात कर सकता हूँ।
एक, दो, तीन, चार, पाँच, छह, सात, आठ, नौ, दस।
ग्यारह, बारह, तेरह, चौदह, पंद्रह, सोलह, सत्रह, अठारह, उन्नीस, बीस।
इक्कीस, बाईस, तेईस, चौबीस, पच्चीस, छब्बीस, सत्ताईस, अट्ठाईस, उनतीस, तीस, इकतीस।
जनवरी, फ़रवरी, मार्च, अप्रैल, मई, जून, जुलाई, अगस्त, सितंबर, अक्टूबर, नवंबर, दिसंबर।
सोमवार, मंगलवार, बुधवार, गुरुवार, शुक्रवार, शनिवार और रविवार।
आज, कल, परसों, अगले हफ़्ते, अगले महीने।
सुबह, दोपहर, शाम, रात, आधा घंटा, एक घंटा, दो घंटे।
गर्मियों में panels पर धूल जल्दी जमती है, इसलिए महीने में एक बार सफ़ाई अच्छी रहती है।
छत पर चढ़ते समय safety का पूरा ध्यान रखा जाता है, आप निश्चिंत रहिए।
हमारे service hero trained हैं और पूरा काम आधे घंटे में खत्म कर देते हैं।
Cleaning के बाद hero आपको photo भी दिखा देंगे।
अगर पौधे या गमले panels के पास रखे हैं तो थोड़ा हटा दीजिएगा।
कुत्ता या कोई pet है तो उस समय बाँध कर रखिएगा, बस safety के लिए।
जी हाँ, पानी हम अपने साथ नहीं लाते, वो site पर ही चाहिए होता है।
आपका पता वही है ना, जो booking में लिखा है?
Location में कोई बदलाव हुआ है तो मुझे अभी बता दीजिए।
आपके area में हमारी team हर गुरुवार को आती है।
मैं आपको दो options देता हूँ, आप जो ठीक लगे वो चुन लीजिए।
पहला option बुधवार सुबह है, और दूसरा शुक्रवार शाम।
अगर दोनों में से कोई नहीं चलेगा तो आप अपनी date बताइए।
""".strip().splitlines()

GENERAL = """
भारत में सौर ऊर्जा तेज़ी से बढ़ रही है और लोग बिजली के बिल बचा रहे हैं।
सुबह की धूप में panels सबसे ज़्यादा बिजली बनाते हैं।
बच्चों की छुट्टियाँ शुरू होते ही घर में चहल-पहल बढ़ जाती है।
मुंबई की बारिश और पुणे की ठंडी हवा दोनों का अपना मज़ा है।
चाय के साथ गरम समोसे मिल जाएँ तो शाम बन जाती है।
ट्रेन थोड़ी लेट थी, पर हम समय पर पहुँच गए।
उसने ध्यान से सुना, थोड़ा सोचा, और फिर मुस्कुरा कर हाँ कह दिया।
क्या आप जानते हैं कि एक साफ़ panel दस से पंद्रह प्रतिशत ज़्यादा बिजली बना सकता है?
झील के किनारे ठंडी हवा चल रही थी और पेड़ों पर चिड़ियाँ चहचहा रही थीं।
ऑफ़िस में meeting के बाद सबने मिलकर खाना खाया।
ज़रूरत पड़ने पर मदद माँगने में कोई शर्म नहीं होनी चाहिए।
फ़ोन की battery कम है, मैं थोड़ी देर में charge करके call करता हूँ।
ग्राहक की संतुष्टि ही हमारी सबसे बड़ी प्राथमिकता है।
ठंड के मौसम में धूप कम होती है, इसलिए generation भी थोड़ा घट जाता है।
उन्होंने ऋषिकेश, हरिद्वार और देहरादून की यात्रा की।
क्षमा कीजिए, क्या आप अपना पूरा नाम बता सकते हैं?
विश्वास, धैर्य और मेहनत से हर काम आसान हो जाता है।
ढाई बजे तक सब तैयार हो जाएगा, फिर हम निकलेंगे।
""".strip().splitlines()


def render_prompts(seed: int) -> list[str]:
    """Every hi.yaml line, rendered by the live flow with varied booking details."""
    import yaml

    from agent.flow import FlowCall, FlowSpec

    rng = random.Random(seed)
    spec = FlowSpec.load()
    hi = yaml.safe_load((spec.dir / "prompts" / "hi.yaml").read_text())
    templates = [(st, k, v) for st, d in hi.items() for k, v in d.items() if isinstance(v, str)]
    out = []
    today = date(2026, 11, 26)
    for i, (st, key, tmpl) in enumerate(templates):
        variants = 3 if re.search(r"\{(weekday|date_spoken|band_spoken|options_spoken|hold_until_spoken)\}", tmpl) else 1
        for _ in range(variants):
            d0 = today + timedelta(days=rng.randint(2, 40))
            if d0.weekday() == 6:
                d0 += timedelta(days=1)
            backups = sorted({today + timedelta(days=rng.randint(3, 45)) for _ in range(3)})
            booking = {"id": "BK-S", "customerName": f"{rng.choice(NAMES)} Kumar", "beatPlan": {
                "primary_slot": {"date": d0.isoformat(), "band": rng.choice(BANDS), "hero_id": "SH-1"},
                "backup_slots": [{"date": b.isoformat(), "band": rng.choice(BANDS), "hero_id": "SH-1"}
                                 for b in backups if b.weekday() != 6]}}
            call = FlowCall(call_id="S", booking=booking, spec=spec, today=today)
            call.lang = "hi"
            call.start()
            call.offered = [{"date": b.isoformat(), "band": rng.choice(BANDS)} for b in backups[:2]]
            call.ctx["missing_items"] = rng.choice(ITEMS)
            call.ctx["hold_until"] = (today + timedelta(days=rng.randint(10, 60))).isoformat()
            call.ctx["recall_window"] = rng.choice(["शाम पाँच बजे के बाद", "कल सुबह", "एक घंटे बाद"])
            rd = today + timedelta(days=rng.randint(2, 20))
            call.ctx["requested_date"] = rd.isoformat()
            call.ctx["requested_band"] = rng.choice(BANDS)
            v = call._vars()
            v["bot_name"] = "Krupal"
            line = re.sub(r"\s+", " ", tmpl.format_map(v)).strip()
            if line and line not in out:
                out.append(line)
    return out


def seconds(lines: list[str]) -> list[float]:
    """Spoken length of each line with the stock Piper voice (Devanagari, as at call time)."""
    from piper import PiperVoice
    from pipecat.services.piper.tts import PIPER_CACHE_DIR

    from agent.devanagari import to_devanagari

    v = PiperVoice.load(PIPER_CACHE_DIR / "hi_IN-rohan-medium.onnx")
    sr = v.config.sample_rate
    return [sum(len(c.audio_int16_bytes) for c in v.synthesize(to_devanagari(t, near_dates=False))) / 2 / sr for t in lines]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=32, help="target spoken length (default 32)")
    ap.add_argument("--out", type=Path, default=HERE / "script_hinglish.txt")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    prompts = render_prompts(a.seed)
    from agent.devanagari import is_english

    prompts = [p for p in prompts if not is_english(p)]  # English sentences go to the English voice at call time
    pools = [prompts, [x.strip() for x in EXTRA if x.strip()], [x.strip() for x in GENERAL if x.strip()]]
    lines, seen = [], set()
    for pool in pools:
        for line in pool:
            if line not in seen:
                seen.add(line)
                lines.append(line)
    secs = seconds(lines)
    total = sum(secs)
    rng = random.Random(a.seed)
    extra_rounds = itertools.count(1)
    while total < a.minutes * 60:  # more renderings of the live lines (new names/dates) until long enough
        for line in render_prompts(a.seed + next(extra_rounds)):
            if line not in seen and total < a.minutes * 60:
                seen.add(line)
                s = seconds([line])[0]
                lines.append(line)
                secs.append(s)
                total += s
    order = list(range(len(lines)))
    rng.shuffle(order)  # mix kinds so tiredness late in the session doesn't hit one kind of line
    with a.out.open("w") as f:
        for n, i in enumerate(order, 1):
            f.write(f"{n:04d}|{lines[i]}\n")
    print(f"{len(lines)} lines, ~{total / 60:.1f} min spoken -> {a.out}")
    print(f"  live call lines: {len(prompts)}, extra call lines: {len(pools[1])}, general: {len(pools[2])}")


if __name__ == "__main__":
    main()
