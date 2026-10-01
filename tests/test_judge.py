"""Tests for the LLM judge (sim/judge.py) and the agreement report, using a fake Gemini client."""

import json
from types import SimpleNamespace

from sim.judge import GeminiJudge, agreement, applicable, build_prompt, case_passes

RUBRIC = [
    {"id": "H1", "name": "Grounded", "applies_to": ["answerable", "unanswerable"],
     "counts_toward_pass": True, "question": "Is every claim supported?"},
    {"id": "H2", "name": "Honest about limits", "applies_to": ["unanswerable"],
     "counts_toward_pass": True, "question": "Does it decline instead of guessing?"},
    {"id": "P2", "name": "Clear", "applies_to": ["answerable", "unanswerable"],
     "counts_toward_pass": False, "question": "Is it clear?"},
]
ITEM = {
    "id": "pi-a-002", "kind": "answerable", "question": "what time do u close on friday",
    "reply": "We close at 1:00 PM on Fridays.", "must_include": ["1:00 PM on Friday"],
    "must_not": ["5:00 PM on Friday"], "expected_decline": None,
    "kb": {"kb-hours-001": "Friday, 8:00 AM to 1:00 PM."}, "cited": ["kb-hours-001"],
}


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.requests.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.payload, Exception):
            raise self.payload
        return SimpleNamespace(text=json.dumps(self.payload))


def test_only_criteria_for_the_case_kind_apply():
    assert [c["id"] for c in applicable(RUBRIC, "answerable")] == ["H1", "P2"]
    assert [c["id"] for c in applicable(RUBRIC, "unanswerable")] == ["H1", "H2", "P2"]


def test_prompt_contains_rubric_question_reply_and_evidence_only_for_this_case():
    prompt = build_prompt(ITEM, applicable(RUBRIC, "answerable"))
    for text in ["Is every claim supported?", "We close at 1:00 PM on Fridays.",
                 "kb-hours-001", "Friday, 8:00 AM to 1:00 PM.", "1:00 PM on Friday", "5:00 PM on Friday"]:
        assert text in prompt
    assert "Does it decline instead of guessing?" not in prompt


def test_prompt_lists_approved_fixed_wording_as_supported():
    # judge-v1 failed H1 on 12 approved decline scripts (run-20261001-061142-48c4).
    from app.agent.scripts import template

    prompt = build_prompt(ITEM, applicable(RUBRIC, "answerable"))
    for name in ("decline_price", "decline_clinical", "decline_unknown", "ask_name", "ask_anything_else"):
        assert template(name) in prompt


def test_client_is_created_once_when_judging_in_parallel(monkeypatch):
    import threading
    import time

    import google.genai

    created = []

    class SlowClient:
        def __init__(self, **kwargs):
            time.sleep(0.05)
            created.append(self)

    monkeypatch.setattr(google.genai, "Client", SlowClient)
    judge = GeminiJudge("judge-test")
    threads = [threading.Thread(target=lambda: judge.client) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(created) == 1


def test_verdicts_are_parsed_per_criterion():
    client = FakeClient({"H1": {"verdict": "pass", "reason": "matches entry"},
                         "P2": {"verdict": "fail", "reason": "rambling"}})
    verdicts = GeminiJudge("judge-test", client=client).judge(ITEM, RUBRIC)
    assert verdicts == {"H1": {"verdict": "pass", "reason": "matches entry"},
                        "P2": {"verdict": "fail", "reason": "rambling"}}
    assert client.requests[0]["model"] == "judge-test"


def test_judge_error_is_reported_not_guessed():
    verdicts = GeminiJudge("judge-test", client=FakeClient(RuntimeError("503"))).judge(ITEM, RUBRIC)
    assert verdicts == {"error": "RuntimeError: 503"}


def test_case_passes_only_on_counted_criteria():
    grades = {"H1": "pass", "P2": "fail"}
    assert case_passes(grades, RUBRIC, "answerable") is True
    assert case_passes({"H1": "fail", "P2": "pass"}, RUBRIC, "answerable") is False


def test_agreement_counts_criteria_and_cases():
    human = {"a": {"H1": "pass", "P2": "pass"}, "b": {"H1": "fail", "P2": "pass"}}
    judge = {"a": {"H1": "pass", "P2": "fail"}, "b": {"H1": "pass", "P2": "pass"}}
    kinds = {"a": "answerable", "b": "answerable"}
    report = agreement(human, judge, RUBRIC, kinds)
    assert report["criteria_agree"] == 2 and report["criteria_total"] == 4
    assert report["cases_agree"] == 1 and report["cases_total"] == 2      # case pass/fail on counted criteria
    assert report["disagreements"] == [
        {"case_id": "a", "criterion": "P2", "human": "pass", "judge": "fail"},
        {"case_id": "b", "criterion": "H1", "human": "fail", "judge": "pass"},
    ]
