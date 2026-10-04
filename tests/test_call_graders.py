"""Rule checks for whole calls (request-capture set), on hand-built transcripts."""

from app.agent.request_store import RequestRecord
from app.agent.scripts import script
from sim.graders import call_checks

CASE = {
    "id": "rc-x",
    "expected": {
        "request_saved": True,
        "request_type": "new_patient",
        "exact": {"name": "Jordan Rivera", "callback_number": "5035550142"},
    },
}
SAVE = {"type": "tool_call", "payload": {"tool": "save_request", "request_id": "req-1"}}
READBACK = "Here's what I have: Jordan Rivera, callback number 5 0 3, 5 5 5, 0 1 4 2 ... Is all of that correct?"


def agent(text, *events):
    return {"role": "agent", "text": text, "events": list(events)}


def caller(text):
    return {"role": "caller", "text": text, "events": []}


def saved(**overrides):
    fields = dict(call_id="c1", type="new_patient", name="Jordan Rivera", callback_number="5035550142",
                  preferred_times="mornings", insurance_carrier="none", reason_for_visit="cleaning")
    return RequestRecord(**(fields | overrides))


def good_transcript():
    return [
        caller("I'd like an appointment"), agent(script("disclosure") + " " + script("ask_name")),
        caller("Jordan Rivera, 503 555 0142 ..."), agent(READBACK),
        caller("Yes"), agent(script("close_request_saved", first_name="Jordan", digits_spoken="5 0 3, 5 5 5, 0 1 4 2"), SAVE),
    ]


def test_complete_call_passes_every_rule():
    checks = call_checks(CASE, good_transcript(), [saved()])
    assert all(c["passed"] for c in checks.values()), checks


def test_wrong_callback_number_fails():
    checks = call_checks(CASE, good_transcript(), [saved(callback_number="5035550124")])
    assert checks["exact_callback_number"]["passed"] is False


def test_name_match_ignores_case_and_spacing():
    checks = call_checks(CASE, good_transcript(), [saved(name="jordan  rivera")])
    assert checks["exact_name"]["passed"] is True


def test_no_saved_request_fails():
    checks = call_checks(CASE, good_transcript()[:4], [])
    assert checks["request_saved"]["passed"] is False


def test_save_without_a_prior_read_back_of_that_number_fails():
    t = good_transcript()
    t[3] = agent("Thanks, saving now.")
    assert call_checks(CASE, t, [saved()])["readback_before_save"]["passed"] is False


def test_reask_of_a_field_already_given_fails():
    case = CASE | {"expected": CASE["expected"] | {"never_reasks": ["name"]}}
    t = good_transcript()
    t.insert(4, agent(script("ask_name")))
    assert call_checks(case, t, [saved()])["never_reasks"]["passed"] is False


def test_required_handoff_event():
    case = CASE | {"expected": CASE["expected"] | {"events": ["handoff"]}}
    assert call_checks(case, good_transcript(), [saved()])["events"]["passed"] is False


def test_after_save_the_agent_must_restate_it_is_only_a_request():
    case = CASE | {"expected": CASE["expected"] | {"after_save_must_not_confirm_booking": True}}
    t = good_transcript() + [caller("So I'm booked for Wednesday then?"), agent(script("ask_how_help"))]
    assert call_checks(case, t, [saved()])["after_save_restates_request"]["passed"] is False
    t[-1] = agent("No, this is a request, not a booked appointment. Our team will follow up.")
    assert call_checks(case, t, [saved()])["after_save_restates_request"]["passed"] is True


def test_expected_script_and_citation_are_found():
    case = CASE | {"expected": CASE["expected"] | {"uses_script": ["decline_price"],
                                                     "answers_side_question_from": ["kb-location-002"]}}
    cite = {"type": "tool_call", "payload": {"tool": "search_kb", "cited": ["kb-location-002"]}}
    t = good_transcript()
    t.insert(2, agent(script("decline_price"), cite))
    checks = call_checks(case, t, [saved()])
    assert checks["uses_script"]["passed"] and checks["answers_side_question"]["passed"]
