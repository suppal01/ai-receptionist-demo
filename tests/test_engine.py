"""Tests for the turn engine: code-controlled stages around a scripted fake model."""

import pytest

from app.agent.engine import Engine
from app.agent.scripts import script
from tests.fakes import FakeModel, step

PHONE = "503 555 0147"
SPOKEN = "5 0 3, 5 5 5, 0 1 4 7"
ALL_FIELDS = dict(
    name="Priya Shah",
    callback_number=PHONE,
    preferred_times="weekday mornings",
    insurance_carrier="Delta Dental PPO",
    reason_for_visit="clear aligners consultation",
)
HOURS_DRAFT = "We're open Friday from 8:00 AM to 1:00 PM."


def run(*steps):
    """Engine with a fake model scripted for the given turns."""
    model = FakeModel(steps)
    return Engine(model), model


def of_type(result, event_type):
    return [e for e in result.events if e["type"] == event_type]


def checks(result):
    return [e["payload"] for e in of_type(result, "check")]


# --- Answers must be grounded ---------------------------------------------------------


def test_grounded_answer_is_sent_with_citation():
    engine, _ = run(step("question", search=["hours"], draft=HOURS_DRAFT, cited=["kb-hours-001"]))
    result = engine.handle("", "When are you open Friday?")
    assert HOURS_DRAFT in result.reply
    assert result.stage == "answer"
    [search] = of_type(result, "tool_call")
    assert search["payload"]["tool"] == "search_kb"
    assert search["payload"]["cited"] == ["kb-hours-001"]


def test_answer_without_a_search_is_replaced_by_decline():
    engine, _ = run(step("question", draft=HOURS_DRAFT, cited=["kb-hours-001"]))
    result = engine.handle("", "When are you open Friday?")
    assert HOURS_DRAFT not in result.reply
    assert script("decline_unknown") in result.reply
    assert checks(result)[0]["passed"] is False


def test_citing_an_entry_the_search_did_not_return_is_replaced_by_decline():
    engine, _ = run(step("question", search=["hours"], draft=HOURS_DRAFT, cited=["kb-team-001"]))
    result = engine.handle("", "When are you open Friday?")
    assert script("decline_unknown") in result.reply


def test_wrong_specific_fact_is_replaced_by_decline():
    wrong = "We're open Friday until 6:00 PM."
    engine, _ = run(step("question", search=["hours"], draft=wrong, cited=["kb-hours-001"]))
    result = engine.handle("", "When are you open Friday?")
    assert wrong not in result.reply
    assert "6:00" in checks(result)[0]["failures"][0]


def test_banned_phrase_in_draft_is_never_sent():
    bad = "We're open Friday from 8:00 AM, and you're booked for then."
    engine, _ = run(step("question", search=["hours"], draft=bad, cited=["kb-hours-001"]))
    result = engine.handle("", "When are you open Friday?")
    assert "booked for" not in result.reply


# --- Prices and clinical questions use fixed scripts ------------------------------------


def test_price_label_uses_decline_script_and_drops_the_draft():
    engine, _ = run(step("price", draft="A cleaning is $99."))
    result = engine.handle("", "What's a cleaning run?")
    assert script("decline_price") in result.reply
    assert "$99" not in result.reply


def test_price_keyword_overrides_a_wrong_label():
    engine, _ = run(step("question", search=["cleaning"], draft="Cleanings are included.", cited=[]))
    result = engine.handle("", "How much does a cleaning cost?")
    assert script("decline_price") in result.reply


def test_clinical_question_uses_decline_script():
    engine, _ = run(step("clinical", draft="Take ibuprofen."))
    result = engine.handle("", "Should I take ibuprofen for my tooth?")
    assert script("decline_clinical") in result.reply
    assert "ibuprofen" not in result.reply.lower()


# --- Collecting a request ----------------------------------------------------------------


def test_request_starts_collecting_and_asks_for_the_first_missing_field():
    engine, _ = run(step("request"))
    result = engine.handle("", "I'd like to make an appointment")
    assert result.stage == "collect"
    assert script("ask_name") in result.reply


def test_details_given_together_are_all_kept_and_only_the_next_is_asked():
    engine, _ = run(step("request", "provide_info", name="Priya Shah", callback_number=PHONE))
    result = engine.handle("", "I'm Priya Shah, 503 555 0147, I want an appointment")
    assert script("ask_preferred_times") in result.reply
    assert script("ask_name") not in result.reply


def test_invalid_phone_is_not_stored_and_is_asked_again():
    engine, _ = run(step("request", "provide_info", name="Priya Shah", callback_number="555 01"))
    result = engine.handle("", "Priya Shah, 555 01")
    assert script("invalid_callback") in result.reply
    assert engine.calls[result.call_id].fields.get("callback_number") is None


def test_question_during_collection_is_answered_then_collection_resumes():
    engine, _ = run(
        step("request", "provide_info", name="Priya Shah", callback_number=PHONE),
        step(
            "provide_info",
            "question",
            search=["parking"],
            draft="Yes, there's free parking in the Lakeview Medical Plaza lot.",
            cited=["kb-location-002"],
            preferred_times="weekday mornings",
        ),
    )
    first = engine.handle("", "I'm Priya Shah, 503 555 0147, I'd like an appointment")
    second = engine.handle(first.call_id, "Weekday mornings. Is there parking?")
    assert "free parking" in second.reply
    assert second.reply.endswith(script("ask_insurance_carrier"))
    assert second.stage == "collect"


def test_all_fields_lead_to_a_scripted_read_back():
    engine, _ = run(step("request", "provide_info", **ALL_FIELDS))
    result = engine.handle("", "everything at once")
    assert result.stage == "confirm"
    assert SPOKEN in result.reply
    assert "Priya Shah" in result.reply


# --- Saving only after a confirmed read-back ----------------------------------------------


def collected(*later_steps):
    engine, model = run(step("request", "provide_info", **ALL_FIELDS), *later_steps)
    first = engine.handle("", "everything at once")
    return engine, first.call_id


def test_confirmation_saves_the_request_and_closes_with_the_fixed_script():
    engine, call_id = collected(step("confirm"))
    result = engine.handle(call_id, "Yes, that's right")
    [save] = [e for e in of_type(result, "tool_call") if e["payload"]["tool"] == "save_request"]
    saved = engine.requests[save["payload"]["request_id"]]
    assert saved.callback_number == "5035550147"
    assert saved.status == "new"
    assert "not a booked appointment" in result.reply
    assert result.stage == "close"


def test_correction_reads_back_again_instead_of_saving():
    engine, call_id = collected(step("correction", callback_number="503 555 0174"), step("confirm"))
    corrected = engine.handle(call_id, "Actually it ends 0174")
    assert "0 1 7 4" in corrected.reply
    assert engine.requests == {}
    confirmed = engine.handle(call_id, "Yes")
    [saved] = engine.requests.values()
    assert saved.callback_number == "5035550174"
    assert confirmed.stage == "close"


def test_denial_asks_what_to_change_and_does_not_save():
    engine, call_id = collected(step("deny"))
    result = engine.handle(call_id, "No, that's wrong")
    assert script("ask_correction") in result.reply
    assert engine.requests == {}


def test_confirm_before_read_back_cannot_save():
    engine, _ = run(step("request", "confirm", name="Priya Shah"))
    result = engine.handle("", "Yes, book it, I'm Priya")
    assert engine.requests == {}
    assert result.stage == "collect"


# --- Guardrail, handoff, goodbye -------------------------------------------------------


def test_emergency_mid_collection_skips_the_model_and_keeps_fields():
    engine, model = run(step("request", "provide_info", name="Priya Shah"))
    first = engine.handle("", "I'm Priya Shah, I want an appointment")
    result = engine.handle(first.call_id, "wait my face is swollen and I have a high fever")
    assert script("emergency") in result.reply
    assert result.stage == "emergency"
    assert len(model.contexts) == 1
    assert engine.calls[first.call_id].fields["name"] == "Priya Shah"
    assert result.events[0]["type"] == "guardrail"


def test_asking_for_a_human_logs_a_handoff():
    engine, _ = run(step("human"))
    result = engine.handle("", "Can I talk to a real person?")
    [handoff] = of_type(result, "handoff")
    assert handoff["payload"]["reason"] == "caller_asked"
    assert script("handoff_human") in result.reply


def test_goodbye_ends_the_call():
    engine, _ = run(step("goodbye"))
    result = engine.handle("", "No, that's all, bye")
    assert result.ended is True
    assert result.reply.endswith(script("close_goodbye"))


def test_first_reply_starts_with_disclosure_and_later_ones_do_not():
    engine, _ = run(step("request"), step("provide_info", name="Priya Shah"))
    first = engine.handle("", "I want an appointment")
    second = engine.handle(first.call_id, "Priya Shah")
    assert first.reply.startswith(script("disclosure"))
    assert script("disclosure") not in second.reply


def test_model_sees_stage_and_missing_fields():
    engine, model = run(step("request"), step("provide_info", name="Priya Shah"))
    first = engine.handle("", "I want an appointment")
    engine.handle(first.call_id, "Priya Shah")
    ctx = model.contexts[1]
    assert ctx.stage == "collect"
    assert ctx.missing_fields[0] == "name"
    assert ctx.history[0] == {"role": "caller", "text": "I want an appointment"}


def test_every_model_turn_is_logged_with_its_name_and_time():
    engine, _ = run(step("request"))
    result = engine.handle("", "I want an appointment")
    [model_event] = of_type(result, "model")
    assert model_event["payload"]["model"] == "fake"
    assert model_event["payload"]["ms"] >= 0
    assert model_event["payload"]["prompt_version"]


def test_model_failure_is_logged_and_the_caller_gets_a_safe_reply():
    from app.agent.model import Interpretation

    engine, _ = run(([], Interpretation(intents=["other"], error="503 unavailable")))
    result = engine.handle("", "When are you open?")
    [model_event] = of_type(result, "model")
    assert model_event["payload"]["error"] == "503 unavailable"
    assert script("ask_how_help") in result.reply


def test_gemini_is_selected_by_agent_model(monkeypatch):
    from app.agent.gemini import GeminiModel
    from app.agent.model import model_from_env

    monkeypatch.setenv("AGENT_MODEL", "gemini-3.8-flash")
    model = model_from_env()
    assert isinstance(model, GeminiModel)
    assert model.name == "gemini-3.8-flash"


# --- The walkthrough call, end to end ----------------------------------------------------


def test_new_patient_call_from_question_to_saved_request():
    engine, model = run(
        step("question", search=["clear aligners"], cited=["kb-services-cosmetic-001"],
             draft="Yes, we offer clear aligners."),
        step("question", search=["Delta Dental"], cited=["kb-insurance-001"],
             draft="Yes, we're in network with Delta Dental PPO."),
        step("price"),
        step("request", "provide_info", name="Priya Shah", callback_number=PHONE),
        step("provide_info", "question", search=["parking"], cited=["kb-location-002"],
             draft="Yes, there's free parking in the Lakeview Medical Plaza lot.",
             preferred_times="weekday mornings"),
        step("provide_info", insurance_carrier="Delta Dental PPO",
             reason_for_visit="clear aligners consultation"),
        step("correction", callback_number="503 555 0174"),
        step("confirm"),
        step("goodbye"),
    )
    call_id = ""
    stages = []
    for text in [
        "Do you do Invisalign?", "Do you take Delta Dental?", "How much would aligners cost?",
        "Sure, Priya Shah, 503 555 0147", "Weekday mornings. Is there parking?",
        "Delta Dental PPO, for aligners", "Actually it ends 0174", "Yes", "No, that's all",
    ]:
        result = engine.handle(call_id, text)
        call_id = result.call_id
        stages.append(result.stage)
    assert stages == [
        "answer", "answer", "answer", "collect", "collect", "confirm", "confirm", "close", "close"
    ]
    assert result.ended is True
    [saved] = engine.requests.values()
    assert saved.callback_number == "5035550174"
    assert saved.reason_for_visit == "clear aligners consultation"
