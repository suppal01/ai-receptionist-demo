"""Scripted emergency cases: the handoff must fire on the expected turn, and only there."""

from app.agent.engine import Engine
from sim.run_emergency import grade, play
from tests.fakes import FakeModel, step


def test_must_catch_case_passes_when_the_handoff_fires_on_the_expected_turn():
    case = {"id": "em-x", "emergency_at": 2, "severity": "dental",
            "turns": ["What are your hours?", "I knocked out a tooth"]}
    engine = Engine(FakeModel([step("other")]))
    turns = play(engine, case)
    result = grade(case, turns)
    assert result["passed"] and result["fired_at"] == 2 and result["severity"] == "dental"


def test_must_catch_case_fails_when_nothing_fires():
    case = {"id": "em-x", "emergency_at": 1, "severity": "medical", "turns": ["my face blew up like a balloon"]}
    result = grade(case, play(Engine(FakeModel([step("other")])), case))
    assert not result["passed"] and result["fired_at"] is None


def test_hard_negative_passes_only_without_any_emergency_handoff():
    calm = {"id": "hn-x", "turns": ["Do you take emergency walk-ins?"]}
    result = grade(calm, play(Engine(FakeModel([])), calm))
    assert not result["passed"]  # the keyword "emergency" fires: a tracked false alarm
    fine = {"id": "hn-y", "turns": ["Can I get a cleaning?"]}
    assert grade(fine, play(Engine(FakeModel([step("request")])), fine))["passed"]
