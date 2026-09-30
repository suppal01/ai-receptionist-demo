"""Tests for the knowledge base seed and search (app/kb)."""

import re
from collections import Counter

import pytest

from app.kb import entries, search


def test_entry_ids_are_unique():
    counts = Counter(e.id for e in entries())
    assert [i for i, n in counts.items() if n > 1] == []


def test_no_entry_mentions_a_price():
    priced = [e.id for e in entries() if re.search(r"\$\s*\d|\d+\s*dollars", e.text, re.I)]
    assert priced == []


@pytest.mark.parametrize(
    "query, expected_ids",
    [
        ("Do you take Delta Dental?", {"kb-insurance-001"}),
        ("Do you take Medicaid?", {"kb-insurance-003"}),
        ("What are your hours?", {"kb-hours-001"}),
        ("When are you open on Friday?", {"kb-hours-001"}),
        ("Where are you located?", {"kb-location-001"}),
        ("Is there parking?", {"kb-location-002"}),
        ("Do you do braces?", {"kb-services-notoffered-001"}),
        ("Do you see kids?", {"kb-services-kids-001"}),
        ("What should I bring to my first visit?", {"kb-newpatient-003"}),
        ("Do you speak Spanish?", {"kb-languages-001", "kb-team-002"}),
        ("Are you accepting new patients?", {"kb-newpatient-001"}),
        ("What is your cancellation policy?", {"kb-policy-cancel-001"}),
    ],
)
def test_top_hit_is_the_right_entry(query, expected_ids):
    hits = search(query)
    assert hits, f"no hits for {query!r}"
    assert hits[0].id in expected_ids


@pytest.mark.parametrize(
    "query",
    ["What's the wifi password?", "Who won the football game last night?"],
)
def test_question_outside_the_kb_returns_nothing(query):
    assert search(query) == []


def test_limit_caps_the_number_of_hits():
    assert len(search("dental insurance plans", limit=2)) <= 2
