"""The AI emergency classifier as a second guardrail layer (it can only add escalations)."""

from app.agent.engine import Engine
from app.agent.scripts import script
from app.guardrail.classifier import ClassifierResult, build_prompt
from tests.fakes import FakeModel, step


class FakeClassifier:
    name = "fake-classifier"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def classify(self, text, history):
        self.calls.append(text)
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


def test_classifier_prompt_includes_recent_context():
    prompt = build_prompt("it's getting worse", [{"role": "caller", "text": "my cheek is swollen"},
                                                 {"role": "agent", "text": "When did it start?"}])
    assert "my cheek is swollen" in prompt and "it's getting worse" in prompt
