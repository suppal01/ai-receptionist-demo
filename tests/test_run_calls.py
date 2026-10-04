"""The simulated-call loop, offline: scripted caller, fake agent model."""

from types import SimpleNamespace

from app.agent.engine import Engine
from sim.run_calls import run_call
from sim.simulator import caller_prompt
from tests.fakes import FakeModel, step

CASE = {
    "id": "rc-x",
    "caller": {
        "persona": "Polite adult.",
        "opening": "I'd like an appointment.",
        "facts": {"name": "Jordan Rivera", "phone": "503 555 0142"},
        "behavior": "Answer what is asked.",
    },
    "max_turns": 6,
}


class ScriptedCaller:
    def __init__(self, lines):
        self.lines = list(lines)
        self.seen = []

    def next_message(self, case, transcript):
        self.seen.append(len(transcript))
        if not self.lines:
            return SimpleNamespace(text="", end_call=True)
        text, end = self.lines.pop(0)
        return SimpleNamespace(text=text, end_call=end)


def test_call_runs_from_opening_until_the_agent_ends_it():
    engine = Engine(FakeModel([
        step("request"),
        step("provide_info", name="Jordan Rivera", callback_number="503 555 0142",
             preferred_times="mornings", insurance_carrier="none", reason_for_visit="cleaning"),
        step("confirm"),
        step("goodbye"),
    ]))
    caller = ScriptedCaller([("Jordan Rivera, 503 555 0142, mornings, no insurance, cleaning", False),
                             ("Yes, that's right", False), ("No, that's all, bye", False)])
    result = run_call(engine, caller, CASE)
    roles = [t["role"] for t in result["transcript"]]
    assert roles == ["caller", "agent"] * 4
    assert result["transcript"][0]["text"] == "I'd like an appointment."
    assert result["ended_by"] == "agent"
    assert [r.callback_number for r in result["saved"]] == ["5035550142"]


def test_caller_can_end_the_call():
    engine = Engine(FakeModel([step("request")]))
    result = run_call(engine, ScriptedCaller([("", True)]), CASE)
    assert result["ended_by"] == "caller"
    assert len(result["transcript"]) == 2


def test_max_turns_stops_a_call_that_goes_nowhere():
    engine = Engine(FakeModel([step("other")] * 6))
    result = run_call(engine, ScriptedCaller([("hmm", False)] * 10), CASE)
    assert result["ended_by"] == "max_turns"
    assert sum(t["role"] == "caller" for t in result["transcript"]) == CASE["max_turns"]


def test_caller_prompt_carries_persona_facts_behavior_and_transcript():
    prompt = caller_prompt(CASE, [{"role": "caller", "text": "I'd like an appointment.", "events": []},
                                  {"role": "agent", "text": "May I have your full name?", "events": []}])
    for text in ["Polite adult.", "Jordan Rivera", "503 555 0142", "Answer what is asked.",
                 "May I have your full name?"]:
        assert text in prompt
