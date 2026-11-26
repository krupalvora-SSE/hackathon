from agent.voice import fix_spoken_text


def test_a_type_is_spoken_as_e_type():
    assert fix_spoken_text("क्या आपके पास A-type ladder है?") == "क्या आपके पास ए टाइप ladder है?"
    assert fix_spoken_text("Do you have an A-type ladder?") == "Do you have an ए टाइप ladder?"
    assert fix_spoken_text("a-type ladder") == "ए टाइप ladder"


def test_other_words_are_untouched():
    for text in ("This is a type of ladder", "maintenance due है", "ladder, पानी और light", "Rahul main aaunga"):
        assert fix_spoken_text(text) == text.replace("main aaunga", "mai aaunga")


def test_existing_main_fix_still_works():
    assert fix_spoken_text("main bol raha hoon") == "mai bol raha hoon"
