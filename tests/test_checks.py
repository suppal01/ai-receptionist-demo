"""Tests for the deterministic checks the code runs on every model draft (app/agent/checks.py)."""

import pytest

from app.agent.checks import (
    asks_about_price,
    find_banned,
    mentions_price,
    normalize_phone,
    speak_digits,
    unsupported_specifics,
)

HOURS = (
    "Sparkle Dental's hours are Monday to Thursday, 8:00 AM to 5:00 PM, and Friday, "
    "8:00 AM to 1:00 PM. The office is closed on Saturday and Sunday."
)


# --- Phone numbers --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["503 555 0147", "(503) 555-0147", "503.555.0147", "1-503-555-0147", "five 503 555 0147"],
)
def test_phone_formats_normalize_to_ten_digits(raw):
    assert normalize_phone(raw) == "5035550147"


@pytest.mark.parametrize("raw", ["555 0147", "503 555 01477 1", "", "call me"])
def test_phone_without_ten_digits_is_rejected(raw):
    assert normalize_phone(raw) is None


def test_phone_is_read_back_in_groups():
    assert speak_digits("5035550147") == "5 0 3, 5 5 5, 0 1 4 7"


# --- Specific facts must appear in the cited source ------------------------------


def test_draft_repeating_source_facts_passes():
    draft = "We're open Monday to Thursday, 8:00 AM to 5:00 PM, and Friday until 1:00 PM."
    assert unsupported_specifics(draft, [HOURS]) == []


def test_wrong_time_is_flagged():
    draft = "We're open until 6:00 PM on Friday."
    assert unsupported_specifics(draft, [HOURS]) == ["6:00"]


def test_name_not_in_source_is_flagged():
    draft = "Yes, we are open on Friday and Dr. Smith is in."
    assert unsupported_specifics(draft, [HOURS]) == ["Smith"]


def test_caller_provided_values_are_allowed():
    draft = "Thanks, Priya. We're open Friday."
    assert unsupported_specifics(draft, [HOURS], allowed=["Priya Shah"]) == []


def test_sentence_start_capitals_are_not_names():
    draft = "Yes. Our office is closed on Sunday."
    assert unsupported_specifics(draft, [HOURS]) == []


# --- Prices and banned phrases ------------------------------------------------------


@pytest.mark.parametrize("text", ["It's $99.", "about 150 dollars", "plans start at 28 per month"])
def test_money_in_reply_is_detected(text):
    assert mentions_price(text)


def test_reply_without_money_passes():
    assert not mentions_price("We're open 8:00 AM to 5:00 PM on Monday.")


@pytest.mark.parametrize(
    "text", ["How much is a cleaning?", "What would it cost?", "Is there a fee?", "what's my copay"]
)
def test_price_questions_are_detected_by_keyword(text):
    assert asks_about_price(text)


def test_insurance_question_is_not_a_price_question():
    assert not asks_about_price("Do you take Delta Dental?")


def test_banned_phrase_is_found_but_required_close_wording_is_not():
    assert find_banned("Great, you're booked for Monday.") == ["you're booked"]
    assert find_banned("This is a request, not a booked appointment.") == []
