"""Tests for the fixed agent wording (app/agent/scripts.yaml)."""

import pytest

from app.agent.scripts import banned_phrases, script, script_names, template


def test_disclosure_says_it_is_an_ai():
    assert "AI receptionist" in script("disclosure")


def test_no_script_contains_a_banned_phrase():
    found = [
        (name, phrase)
        for name in script_names()
        for phrase in banned_phrases()
        if phrase.lower() in template(name).lower()
    ]
    assert found == []


def test_close_says_it_is_not_a_booked_appointment():
    text = script("close_request_saved", first_name="Sam", digits_spoken="5 5 5")
    assert "not a booked appointment" in text
    assert "Sam" in text


def test_missing_placeholder_value_raises():
    with pytest.raises(KeyError):
        script("close_request_saved", first_name="Sam")
