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


@pytest.mark.parametrize(
    "text, rule_id",
    [
        # Possessives and extra words between key words (emergency_v1 em-018, em-013).
        ("My son knocked out his front tooth playing soccer", "em-tooth-trauma"),
        ("My daughter fell, hit her head and knocked out a tooth", "em-trauma"),
        # Small typos and missing apostrophes (em-030).
        ("bleding wont stop after my extraction", "em-bleeding"),
        ("i cant breath", "em-breathing"),
    ],
)
def test_wording_variants_still_match(text, rule_id):
    match = check(text)
    assert match is not None and match.rule_id == rule_id


def test_head_injury_outranks_the_knocked_out_tooth():
    # em-013: a fall with a head injury is medical, not only dental.
    assert check("My daughter fell, hit her head and knocked out a tooth").severity == "medical"


def test_typo_tolerance_does_not_turn_short_words_into_matches():
    # "pus" (3 letters) must not match "plus" or "bus".
    assert check("I'll take the bus, plus I need a cleaning") is None


def test_phrase_inside_another_word_does_not_match():
    # "pus" must not match inside "campus".
    assert check("I'm on campus until noon") is None
