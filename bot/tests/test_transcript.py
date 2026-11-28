"""The spoken transcript: Bot / Human lines, saved next to the call's audio."""
from agent import transcript as T


def test_bot_and_human_lines_saved_and_copyable(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "transcript_dir", lambda call_id: tmp_path / call_id)
    tr = T.CallTranscript("CALL-test")
    tr.bot("हॅलो?")
    tr.human("Hello.")
    tr.bot("जी, एक मिनट।")  # a filler: said, though never in the written chat
    tr.bot("  राहुल जी,\n आपके   सौलर सिस्टम का मेंटेनेंस ड्यू है।")
    tr.interrupted()
    tr.human("   ")  # empty turn is dropped
    tr.save()

    lines = T.load("CALL-test")
    assert [(e["who"], e["text"]) for e in lines] == [
        ("bot", "हॅलो?"), ("human", "Hello."), ("bot", "जी, एक मिनट।"),
        ("bot", "राहुल जी, आपके सौलर सिस्टम का मेंटेनेंस ड्यू है।"),
    ]
    assert lines[-1]["interrupted"] and "interrupted" not in lines[0]
    assert all(isinstance(e["t"], float) for e in lines)
    assert (tmp_path / "CALL-test" / T.TXT).read_text() == (
        "Bot: हॅलो?\nHuman: Hello.\nBot: जी, एक मिनट।\n"
        "Bot: राहुल जी, आपके सौलर सिस्टम का मेंटेनेंस ड्यू है। [interrupted]\n")


def test_unsafe_call_id_is_refused():
    assert T.transcript_dir("../etc") is None
    assert T.load("../etc") is None
