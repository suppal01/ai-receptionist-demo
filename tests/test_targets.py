"""Eval targets shared by the deploy gate and the dashboard."""

from app.dashboard.targets import targets_for

PASS = {"verdict": "pass", "reason": ""}
FAIL = {"verdict": "fail", "reason": "x"}
OK_RULES = {"r": {"passed": True, "detail": ""}}


def test_practice_info_targets():
    rows = [("a1", "answerable", {"H1": PASS}, True, OK_RULES)] * 49 + [("a2", "answerable", {"H1": FAIL}, False, OK_RULES)]
    rows += [("u1", "unanswerable", {"H1": PASS}, True, OK_RULES)] * 20
    ground, decline = targets_for("practice_info", rows)
    assert (ground.got, ground.total, ground.met) == (49, 50, True)   # 98% meets 98%
    assert (decline.got, decline.total, decline.met) == (20, 20, True)


def test_request_capture_needs_every_callback_number_exact():
    good = {"exact_callback_number": {"passed": True, "detail": ""}}
    bad = {"exact_callback_number": {"passed": False, "detail": ""}}
    rows = [("rc", "call", {}, True, good)] * 9 + [("rc", "call", {}, True, bad)]
    complete, callback = targets_for("request_capture", rows)
    assert complete.met and not callback.met


def test_emergency_false_alarms_are_tracked_not_required():
    rows = [("em", "must_catch", None, True, {})] * 3 + [("hn", "hard_negative", None, False, {})] * 2
    recall, alarms = targets_for("emergency", rows)
    assert recall.met and (alarms.got, alarms.counted, alarms.met) == (2, False, True)
