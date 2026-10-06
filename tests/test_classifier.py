"""The AI emergency classifier as a second guardrail layer (it can only add escalations)."""

from app.agent.engine import Engine
from app.agent.scripts import script
from app.guardrail.classifier import ClassifierResult, build_prompt
from tests.fakes import FakeModel, step


class FakeClassifier:
    name = "fake-classifier"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls, self.already_escalated = result, error, [], []

    def classify(self, text, history, already_escalated=False):
        self.calls.append(text)
        self.already_escalated.append(already_escalated)
        if self.error:
            raise self.error
        return self.result


EMERGENCY = ClassifierResult(emergency=True, severity="medical", reason="face swelling with breathing trouble")
ROUTINE = ClassifierResult(emergency=False, severity="none", reason="asks about hours")


def of_type(result, event_type):
    return [e for e in result.events if e["type"] == event_type]


def test_classifier_catches_what_keywords_miss_and_the_agent_turn_is_discarded():
    classifier = FakeClassifier(EMERGENCY)
    engine = Engine(FakeModel([step("request", "provide_info", name="Jo Doe")]), classifier=classifier)
    result = engine.handle("", "I can barely get air in and my mouth is puffing up")
    assert script("emergency") in result.reply
    assert result.stage == "emergency"
    [handoff] = of_type(result, "handoff")
    assert handoff["payload"] == {"reason": "emergency", "severity": "medical", "source": "classifier"}
    guards = of_type(result, "guardrail")
    assert guards[0]["payload"]["triggered"] is False          # keywords: no match
    assert guards[1]["payload"]["source"] == "classifier"      # classifier: emergency
    assert guards[1]["payload"]["triggered"] is True
    assert engine.calls[result.call_id].fields == {}           # the agent's turn changed nothing


def test_routine_message_goes_through_normally():
    engine = Engine(FakeModel([step("request")]), classifier=FakeClassifier(ROUTINE))
    result = engine.handle("", "I'd like an appointment")
    assert result.stage == "collect"
    assert of_type(result, "guardrail")[1]["payload"]["triggered"] is False


def test_classifier_failure_never_blocks_the_reply():
    engine = Engine(FakeModel([step("request")]), classifier=FakeClassifier(error=TimeoutError("slow")))
    result = engine.handle("", "I'd like an appointment")
    assert result.stage == "collect"
    assert "slow" in of_type(result, "guardrail")[1]["payload"]["error"]


def test_keyword_match_escalates_without_asking_the_classifier():
    classifier = FakeClassifier(ROUTINE)
    engine = Engine(FakeModel([]), classifier=classifier)
    result = engine.handle("", "I knocked out a tooth")
    assert result.stage == "emergency"
    assert classifier.calls == []


def test_classifier_prompt_says_routine_dental_problems_are_not_emergencies():
    # rc-003: "I have a loose filling" was flagged as a dental emergency.
    from app.guardrail.classifier import SYSTEM

    assert "loose or lost filling" in SYSTEM


def test_dental_severity_gets_the_dental_script_without_911():
    # Chat testing 2026-10-05: a loose-crown caller kept hearing the 911 message.
    dental = ClassifierResult(emergency=True, severity="dental", reason="knocked-out tooth")
    engine = Engine(FakeModel([step("other")]), classifier=FakeClassifier(dental))
    result = engine.handle("", "my kid's tooth came out in a fall")
    assert script("emergency_dental") in result.reply
    assert "911" not in result.reply


def test_medical_severity_keeps_the_911_script():
    engine = Engine(FakeModel([]))
    result = engine.handle("", "I can't breathe and my face is swollen")
    assert script("emergency") in result.reply


def test_classifier_is_told_when_emergency_instructions_were_already_given():
    # Chat call 909c4f49: after "I don't have any trouble as you described", giving a phone
    # number re-triggered the classifier on pain mentioned two turns earlier.
    classifier = FakeClassifier(ROUTINE)
    engine = Engine(FakeModel([step("provide_info", name="Lakshmi")]), classifier=classifier)
    call_id = engine.handle("", "I knocked out a tooth").call_id      # keywords escalate
    engine.handle(call_id, "my name is Lakshmi, no breathing trouble")
    assert classifier.already_escalated == [True]


def test_classifier_prompt_v3_rules():
    from app.guardrail.classifier import SYSTEM, build_prompt

    assert "some tooth pain" in SYSTEM or "mild or unspecified" in SYSTEM
    assert "attachment" in SYSTEM
    assert "new or worse" in build_prompt("hi", [], already_escalated=True)
    assert "new or worse" not in build_prompt("hi", [], already_escalated=False)


def test_classifier_prompt_includes_recent_context():
    prompt = build_prompt("it's getting worse", [{"role": "caller", "text": "my cheek is swollen"},
                                                 {"role": "agent", "text": "When did it start?"}])
    assert "my cheek is swollen" in prompt and "it's getting worse" in prompt
