"""Tests for the rule checks the eval runner applies to each test case (sim/graders.py)."""

from app.agent.scripts import script
from sim.graders import rule_checks
from sim.testsets import load_test_set

ANSWERABLE = {
    "id": "pi-a-002",
    "kind": "answerable",
    "question": "what time do u close on friday",
    "expected_ids": ["kb-hours-001"],
    "must_include": ["1:00 PM on Friday"],
    "must_not": ["5:00 PM on Friday"],
}
PRICE = {"id": "pi-u-009", "kind": "unanswerable", "question": "How much is a cleaning?",
         "expected": "decline_price"}


def reply(text, cited=(), first=True):
    return (script("disclosure") + " " if first else "") + text, [
        {"type": "tool_call", "payload": {"tool": "search_kb", "cited": list(cited)}}
    ]


def test_answerable_with_expected_citation_passes_rules():
    text, events = reply("We close at 1:00 PM on Fridays.", cited=["kb-hours-001"])
    checks = rule_checks(ANSWERABLE, text, events)
    assert all(c["passed"] for c in checks.values()), checks


def test_missing_or_wrong_citation_fails():
    text, events = reply("We close at 1:00 PM on Fridays.", cited=["kb-hours-002"])
    assert rule_checks(ANSWERABLE, text, events)["cited_expected"]["passed"] is False


def test_must_not_phrase_fails():
    text, events = reply("We close at 5:00 PM on Friday.", cited=["kb-hours-001"])
    assert rule_checks(ANSWERABLE, text, events)["must_not"]["passed"] is False


def test_price_in_reply_fails():
    text, events = reply("A cleaning is $99.", cited=["kb-hours-001"])
    assert rule_checks(ANSWERABLE, text, events)["no_price"]["passed"] is False


def test_missing_disclosure_fails():
    text, events = reply("We close at 1:00 PM on Fridays.", cited=["kb-hours-001"], first=False)
    assert rule_checks(ANSWERABLE, text, events)["disclosure"]["passed"] is False


def test_unanswerable_needs_the_expected_decline_script():
    good, events = reply(script("decline_price"))
    wrong, _ = reply(script("decline_unknown"))
    assert rule_checks(PRICE, good, events)["expected_decline"]["passed"] is True
    assert rule_checks(PRICE, wrong, events)["expected_decline"]["passed"] is False


def test_answerable_that_was_declined_fails():
    text, events = reply(script("decline_unknown"))
    assert rule_checks(ANSWERABLE, text, events)["not_declined"]["passed"] is False


def test_practice_info_v1_loads_and_is_well_formed():
    ts = load_test_set("testsets/practice_info_v1.yaml")
    assert ts["test_set"] == "practice_info" and ts["version"] == 1
    assert len(ts["cases"]) == 60


def test_request_capture_v1_loads_as_a_multi_turn_set():
    ts = load_test_set("testsets/request_capture_v1.yaml")
    assert ts["test_set"] == "request_capture" and len(ts["cases"]) == 15
    assert all("caller" in c and "expected" in c for c in ts["cases"])
