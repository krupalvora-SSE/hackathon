import asyncio

import pytest

from agent import knowledge
from agent.engine import FlowEngine
from agent.flow import FlowCall, FlowSpec
from agent.prompts import flow_system_prompt
from test_flow import BOOKING, TODAY, live

SPEC = FlowSpec.load()
TAUGHT = {
    "faqs": [{"id": "f1", "question": "Technician ka number milega?", "answer": "Technician visit वाले दिन आपको call करेगा।"}],
    "intents": {"wants_human": ["mujhe manager se baat karni hai"], "do_not_call": ["mat karo call"]},
}


def call_with(knowledge_=None, lang="hi"):
    call = FlowCall(call_id="K", booking=BOOKING, spec=SPEC, today=TODAY, knowledge=knowledge_ or {})
    call.lang = lang
    call.start()
    return call


# ---- what counts as an off-guard turn
def test_each_way_of_not_handling_a_turn_is_flagged_with_its_reason():
    call = live()[0]
    call.handle("affirm")
    assert call.handle("price_or_offtopic").offguard == "deflected"
    assert call.handle("unclear").offguard == "unclear"
    assert call.handle("other_language").offguard == "other_language"
    call.handle("proposes_date", {"date": "kuch bhi"})
    # a date nobody can read is "not_understood"
    assert call.handle("proposes_date", {"date": "kuch bhi"}).offguard == "not_understood"


def test_turns_we_handled_are_not_flagged():
    call = live()[0]
    for intent in ("affirm", "asks_price", "ask_time", "who_are_you"):
        assert call.handle(intent).offguard is None
    assert call.handle("repeat_request").offguard is None


def test_the_flag_reaches_the_bot_through_the_engine_action():
    e = FlowEngine("E", BOOKING)
    e.dispatch("customer_said", {"intent": "affirm"})
    assert e.dispatch("customer_said", {"intent": "price_or_offtopic"}).offguard == "deflected"
    assert e.dispatch("customer_said", {"intent": "ask_time"}).offguard is None


def test_the_handover_after_two_deflections_is_flagged_too():
    call = live()[0]
    call.handle("affirm")
    call.handle("price_or_offtopic")
    call.handle("price_or_offtopic")
    r = call.handle("price_or_offtopic")
    assert r.end and r.offguard == "deflected"


# ---- approved answers
def test_an_approved_answer_is_spoken_then_the_question_is_asked_again():
    call = call_with(TAUGHT)
    call.handle("affirm")
    r = call.handle("known_question", {"faq_id": "f1"})
    assert r.speak[0].startswith("Technician visit वाले दिन आपको call करेगा।") and r.speak[0].endswith(call._reask())
    assert r.offguard is None and call.state == "OFFER_PRIMARY"


def test_an_approved_answer_can_be_used_again_and_never_escalates():
    call = call_with(TAUGHT)
    call.handle("affirm")
    for _ in range(5):
        r = call.handle("known_question", {"faq_id": "f1"})
        assert not r.end and "Technician" in r.speak[0]


def test_an_unknown_faq_id_falls_back_to_the_deflection():
    call = call_with(TAUGHT)
    call.handle("affirm")
    r = call.handle("known_question", {"faq_id": "nope"})
    assert r.speak[0].startswith(call._global_line("deflection")) and r.offguard == "deflected"
    call = call_with()                                              # nothing taught at all
    call.handle("affirm")
    assert call.handle("known_question", {"faq_id": "f1"}).offguard == "deflected"


# ---- training the classifier
def test_the_prompt_lists_approved_answers_and_taught_phrases():
    prompt = flow_system_prompt(call_with(TAUGHT))
    assert 'faq_id=f1: "Technician ka number milega?"' in prompt
    assert "Technician visit वाले दिन" not in prompt                  # the model picks the id; the system speaks it
    wants = next(line for line in prompt.splitlines() if line.startswith("- wants_human"))
    assert "mujhe manager se baat karni hai" in wants
    dnc = next(line for line in prompt.splitlines() if line.startswith("- do_not_call"))
    assert "mat karo call" in dnc

    assert "same topic and need" in prompt and "even when the wording" in prompt   # reworded / 'yes, but' questions still match
    assert "asks_meaning, never an approved answer" in prompt                      # but "what does X mean" does not


def test_without_anything_taught_the_prompt_has_no_approved_section():
    prompt = flow_system_prompt(call_with())
    assert "Approved answers: when" not in prompt and "faq_id=" not in prompt


def test_the_model_can_only_pick_known_question_with_a_real_faq_id():
    from agent.tools import flow_tools

    tool = flow_tools(SPEC, TAUGHT)[0]["properties"]
    assert "known_question" in tool["intent"]["enum"] and tool["faq_id"]["enum"] == ["f1"]


def test_with_nothing_taught_known_question_and_faq_id_do_not_exist_for_the_model():
    from agent.tools import flow_tools

    for taught in (None, {}, {"faqs": [], "intents": {"wants_human": ["x"]}}):
        tool = flow_tools(SPEC, taught)[0]["properties"]
        assert "known_question" not in tool["intent"]["enum"] and "faq_id" not in tool
    prompt = flow_system_prompt(call_with())
    assert not any(line.startswith("- known_question") for line in prompt.splitlines())
    assert any(line.startswith("- known_question") for line in flow_system_prompt(call_with(TAUGHT)).splitlines())


def test_the_allowed_intents_the_model_is_told_hide_known_question_until_something_is_taught():
    e = FlowEngine("E", BOOKING)
    assert "known_question" not in e.dispatch("customer_said", {"intent": "affirm"}).result["allowed_intents"]
    t = FlowEngine("T", BOOKING, knowledge=TAUGHT)
    assert "known_question" in t.dispatch("customer_said", {"intent": "affirm"}).result["allowed_intents"]
    assert t.tools[0]["properties"]["faq_id"]["enum"] == ["f1"]


# ---- reading what the admin taught
def test_clean_keeps_only_well_formed_entries_and_caps_them():
    data = {"faqs": [{"id": "a", "question": "q", "answer": "x" * 900}, {"id": "b", "question": "", "answer": "a"},
                     "junk", {"question": "q", "answer": "a"}] + [{"id": str(i), "question": "q", "answer": "a"} for i in range(60)],
            "intents": {"wants_human": ["p"] * 30, "bad": "not a list"}}
    out = knowledge.clean(data)
    assert len(out["faqs"]) == 40 and out["faqs"][0]["answer"] == "x" * 400 and "bad" not in out["intents"]
    assert len(out["intents"]["wants_human"]) == 20
    assert knowledge.clean("junk") == {} and knowledge.clean(None) == {}


def test_a_down_app_means_calling_without_taught_answers(monkeypatch):
    monkeypatch.setenv("APP_URL", "http://127.0.0.1:9")            # nothing listens there
    assert asyncio.run(knowledge.fetch_knowledge()) == {}


def test_the_flow_outline_lists_intents_for_the_admin_view():
    import server

    out = asyncio.run(server.flow_outline())
    names = {i["name"] for i in out["intents"]}
    assert {"wants_human", "do_not_call", "known_question", "affirm"} <= names
    assert all(i["label"] and i["scope"] in ("any time", "one step") for i in out["intents"])
