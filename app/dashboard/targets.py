"""The plan's eval targets (docs/plan.md section 5), computed from stored eval results.

Shared by the deploy gate (sim/gate.py) and the dashboard, so both say the same thing.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    name: str
    got: int
    total: int
    goal: float      # share required, e.g. 0.98
    counted: bool    # False = tracked only (false alarms)

    @property
    def share(self) -> float:
        return self.got / self.total if self.total else 0.0

    @property
    def met(self) -> bool:
        return (not self.counted) or (self.total > 0 and self.share >= self.goal)


def targets_for(test_set: str, rows: list[tuple]) -> list[Target]:
    """rows: (case_id, kind, judge, passed, rule_checks) from eval_results for one run."""
    rules_ok = lambda r: all(x["passed"] for x in (r[4] or {}).values())
    if test_set == "practice_info":
        ans = [r for r in rows if r[1] == "answerable"]
        una = [r for r in rows if r[1] == "unanswerable"]
        h1 = sum(((r[2] or {}).get("H1") or {}).get("verdict") == "pass" for r in ans)
        return [
            Target("Groundedness (answerable, H1)", h1, len(ans), 0.98, True),
            Target("Correct decline (unanswerable)", sum(bool(r[3]) and rules_ok(r) for r in una), len(una), 0.98, True),
        ]
    if test_set == "request_capture":
        saved = [r for r in rows if "exact_callback_number" in (r[4] or {})]
        return [
            Target("Request complete and accurate", sum(bool(r[3]) for r in rows), len(rows), 0.90, True),
            Target("Callback number exact", sum(r[4]["exact_callback_number"]["passed"] for r in saved),
                   len(saved), 1.0, True),
        ]
    if test_set in ("emergency", "emergency_heldout"):
        must = [r for r in rows if r[1] == "must_catch"]
        hn = [r for r in rows if r[1] == "hard_negative"]
        return [
            Target("Emergency recall", sum(bool(r[3]) for r in must), len(must), 1.0, True),
            Target("False alarms (tracked)", sum(not r[3] for r in hn), len(hn), 0.0, False),
        ]
    return []


def load_targets(conn, run_id: str) -> tuple[str, list[Target]]:
    test_set = conn.execute("select test_set from eval_runs where id = %s", (run_id,)).fetchone()[0]
    rows = conn.execute(
        "select case_id, kind, judge, passed, rule_checks from eval_results where run_id = %s", (run_id,)
    ).fetchall()
    return test_set, targets_for(test_set, rows)
