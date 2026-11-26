"""voice_training/prepare.py alignment: recordings read in order, across several files."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "voice_training"))
import prepare  # noqa: E402

SCRIPT = [(f"{i:04d}", t) for i, t in enumerate([
    "नमस्ते, क्या राहुल जी बोल रहे हैं?", "आपके solar system का maintenance due है।", "क्या आपके पास A-type ladder है?",
    "पैनल के पास पानी का नल है क्या?", "और क्या वहाँ बिजली का कनेक्शन है?", "आपकी booking confirm हो गई है।",
    "कोई बात नहीं, आप आराम से बताइए।", "माफ़ कीजिए, आवाज़ साफ़ नहीं आ रही थी।", "सोमवार से शनिवार तक visit हो सकती है।",
    "रविवार को हमारी team छुट्टी पर रहती है।", "धन्यवाद, आपका दिन शुभ हो।", "अगर पानी नहीं है तो एक बाल्टी पानी भी चल जाएगा।",
    "Building के roof तक जाने के लिए सीढ़ी है?", "Weekend पर slot थोड़ा मुश्किल से मिलता है।", "आज, कल, परसों, अगले हफ़्ते।",
    "सुबह, दोपहर, शाम, रात।",
], 1)]


def test_second_recording_continues_where_the_first_stopped():
    first = [t for _, t in SCRIPT[:8]]
    second = [t for _, t in SCRIPT[8:]]          # a new file starting at line 9: used to match nothing
    m1, _, ptr = prepare.align(first, SCRIPT)
    m2, dropped, _ = prepare.align(second, SCRIPT, ptr)
    assert [j for j, *_ in m1] == list(range(8)) and [j for j, *_ in m2] == list(range(8, 16)) and not dropped


def test_reanchors_after_a_skipped_block():
    said = ["हम्म", "ओह", "जी", "अच्छा", "हाँ", "ठीक"] + [t for _, t in SCRIPT[12:]]   # noise, then line 13 on
    m, _, _ = prepare.align(said, SCRIPT, 0)
    assert [j for j, *_ in m] == [12, 13, 14, 15]
