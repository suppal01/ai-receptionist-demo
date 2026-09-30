"""Tests for the emergency keyword guardrail (app/guardrail)."""

import pytest

from app.guardrail import check, examples

SHOULD_TRIGGER = examples()["should_trigger"]


@pytest.mark.parametrize("text", SHOULD_TRIGGER)
def test_every_must_catch_example_triggers(text):
    assert check(text) is not None


def test_routine_request_does_not_trigger():
    assert check("I'd like to book a cleaning for next month") is None


def test_match_reports_rule_severity_and_phrase():
    match = check("I think I have an ABSCESS")
    assert match.rule_id == "em-abscess"
    assert match.severity == "dental"
    assert match.phrase == "abscess"


def test_medical_rule_wins_over_dental_rule():
    # "knocked out a tooth" is dental, "can't breathe" is medical.
    match = check("I knocked out a tooth and I can't breathe")
    assert match.severity == "medical"


def test_curly_apostrophe_still_matches():
    assert check("I can’t breathe") is not None


def test_extra_spaces_and_line_breaks_still_match():
    assert check("my  face is\nswollen") is not None


def test_phrase_inside_another_word_does_not_match():
    # "pus" must not match inside "campus".
    assert check("I'm on campus until noon") is None
