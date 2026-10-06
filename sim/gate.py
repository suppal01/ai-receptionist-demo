"""Deploy gate: check one run of each test set against the plan's targets (docs/plan.md section 5).

    python -m sim.gate pi=RUN_ID rc=RUN_ID em=RUN_ID eh=RUN_ID


Prints PASS/FAIL per target and exits 0 only if every target is met.
"""

import sys

import psycopg
from dotenv import dotenv_values

runs = dict(arg.split("=", 1) for arg in sys.argv[1:])  # pi=..., rc=..., em=..., eh=...
ok = True
with psycopg.connect(dotenv_values(".env")["DATABASE_URL"]) as c:
    def results(run):
        return c.execute("select case_id, kind, judge, passed, rule_checks from eval_results where run_id = %s",
                         (run,)).fetchall()

    def report(name, got, total, target):
        global ok
        passed = total and got / total >= target
        ok = ok and bool(passed)
        print(f"{'PASS' if passed else 'FAIL'} {name}: {got}/{total} = {100 * got / total:.1f}% (target {100 * target:.0f}%)")

    pi = results(runs["pi"])
    rules_ok = lambda r: all(x["passed"] for x in r[4].values())
    ans = [r for r in pi if r[1] == "answerable"]
    una = [r for r in pi if r[1] == "unanswerable"]
    report("groundedness (pi H1)", sum((r[2] or {}).get("H1", {}).get("verdict") == "pass" for r in ans), len(ans), 0.98)
    report("correct decline (pi)", sum(bool(r[3]) and rules_ok(r) for r in una), len(una), 0.98)

    rc = results(runs["rc"])
    report("request complete and accurate (rc)", sum(bool(r[3]) for r in rc), len(rc), 0.90)
    saved = [r for r in rc if "exact_callback_number" in r[4]]
    report("callback number exact (rc)", sum(r[4]["exact_callback_number"]["passed"] for r in saved), len(saved), 1.0)

    for key, name in (("em", "emergency recall (development)"), ("eh", "emergency recall (held-out)")):
        rows = results(runs[key])
        must = [r for r in rows if r[1] == "must_catch"]
        report(name, sum(bool(r[3]) for r in must), len(must), 1.0)
        hn = [r for r in rows if r[1] == "hard_negative"]
        print(f"     false alarms (tracked): {sum(not r[3] for r in hn)}/{len(hn)}")

sys.exit(0 if ok else 1)
