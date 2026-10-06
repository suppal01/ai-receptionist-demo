"""Deploy gate: check one run of each test set against the plan's targets (docs/plan.md section 5).

    python -m sim.gate pi=RUN_ID rc=RUN_ID em=RUN_ID eh=RUN_ID

Prints PASS/FAIL per target and exits 0 only if every counted target is met. The targets
themselves live in app/dashboard/targets.py, shared with the dashboard.
"""

import sys

import psycopg
from dotenv import dotenv_values

from app.dashboard.targets import load_targets


def main() -> int:
    runs = dict(arg.split("=", 1) for arg in sys.argv[1:])  # pi=..., rc=..., em=..., eh=...
    ok = True
    with psycopg.connect(dotenv_values(".env")["DATABASE_URL"]) as conn:
        for run_id in runs.values():
            test_set, targets = load_targets(conn, run_id)
            for t in targets:
                if not t.counted:
                    print(f"     {t.name} [{test_set}]: {t.got}/{t.total}")
                    continue
                ok = ok and t.met
                print(f"{'PASS' if t.met else 'FAIL'} {t.name} [{test_set}]: {t.got}/{t.total} = "
                      f"{100 * t.share:.1f}% (target {100 * t.goal:.0f}%)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
