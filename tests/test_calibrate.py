"""Loading calibration inputs: the seeded set and grades exported from the grading page."""

import json

from sim.calibrate import SEEDED_RUN_ID, load_grades, load_seeded


def test_seeded_items_take_question_expectations_and_kb_from_their_case():
    items = {i["id"]: i for i in load_seeded("sim/calibration/seeded_v1.yaml")}
    assert len(items) == 16
    wrong_time = items["s01-wrong-time"]
    assert wrong_time["kind"] == "answerable"
    assert wrong_time["question"] == "what time do u close on friday"
    assert wrong_time["reply"] == "We close at 5:00 PM on Fridays."
    assert "kb-hours-001" in wrong_time["kb"] and wrong_time["cited"] == ["kb-hours-001"]
    assert items["s04-aligner-brand"]["kind"] == "unanswerable"
    assert items["s04-aligner-brand"]["expected_decline"] == "decline_unknown"


def test_only_grades_for_the_requested_run_are_loaded(tmp_path):
    (tmp_path / "s01.json").write_text(json.dumps(
        {"case_id": "s01-wrong-time", "run_id": SEEDED_RUN_ID, "H1": "fail", "P1": "fail", "P2": "pass"}))
    (tmp_path / "a.json").write_text(json.dumps({"case_id": "pi-a-001", "run_id": "run-x", "H1": "pass"}))
    assert load_grades(str(tmp_path), SEEDED_RUN_ID) == {"s01-wrong-time": {"H1": "fail", "P1": "fail", "P2": "pass"}}
