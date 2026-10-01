"""The eval runner's core, offline: KB-only model, in-memory store."""

from app.agent.engine import Engine
from app.agent.model import KBOnlyModel
from sim.run_eval import run_cases, summarize

CASES = [
    {"id": "a1", "kind": "answerable", "question": "Do you take Delta Dental?",
     "expected_ids": ["kb-insurance-001"], "must_include": ["Delta Dental PPO"]},
    {"id": "u1", "kind": "unanswerable", "question": "What's the wifi password?",
     "expected": "decline_unknown"},
]


def test_each_case_runs_as_its_own_new_call_with_rule_checks():
    results = run_cases(Engine(KBOnlyModel()), CASES)
    assert [r["case_id"] for r in results] == ["a1", "u1"]
    assert results[0]["call_id"] != results[1]["call_id"]
    assert results[0]["cited"] == ["kb-insurance-001"]
    assert results[0]["rule_checks"]["cited_expected"]["passed"] is True
    assert results[1]["rule_checks"]["expected_decline"]["passed"] is True
    assert results[0]["latency_ms"] >= 0


def test_summary_counts_rule_passes_by_kind():
    results = run_cases(Engine(KBOnlyModel()), CASES)
    summary = summarize(results)
    assert summary["cases"] == 2
    assert summary["rules_passed"] == {"answerable": 1, "unanswerable": 1}
