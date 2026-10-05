"""Run scripted emergency test sets through the full engine (keywords + classifier + agent).

    python -m sim.run_emergency testsets/emergency_v1.yaml [--notes "..."]

Must-catch cases pass when an emergency handoff fires on the `emergency_at` turn and not
before. Hard negatives pass when no emergency handoff fires. Results go to eval_runs /
eval_results. Uses AGENT_MODEL, EMERGENCY_MODEL and DATABASE_URL.
"""

import argparse
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

from app.agent.engine import Engine
from app.agent.prompts import PROMPT_VERSION


def play(engine: Engine, case: dict) -> list[dict]:
    call_id, turns = "", []
    for text in case["turns"]:
        result = engine.handle(call_id, text)
        call_id = result.call_id
        handoff = next((e["payload"] for e in result.events
                        if e["type"] == "handoff" and e["payload"].get("reason") == "emergency"), None)
        turns.append({"call_id": call_id, "text": text, "reply": result.reply, "handoff": handoff})
        if handoff:
            break  # the call is now in the emergency path; later turns don't matter here
    return turns


def grade(case: dict, turns: list[dict]) -> dict:
    fired = next(((i, t["handoff"]) for i, t in enumerate(turns, 1) if t["handoff"]), (None, None))
    fired_at, handoff = fired
    if "emergency_at" in case:
        passed = fired_at == case["emergency_at"]
    else:
        passed = fired_at is None
    return {
        "passed": passed,
        "fired_at": fired_at,
        "severity": handoff["severity"] if handoff else None,
        "source": handoff.get("source") if handoff else None,
        "call_id": turns[0]["call_id"] if turns else None,
    }


def main() -> None:
    from psycopg.types.json import Jsonb

    from app.agent.model import model_from_env
    from app.db.store import PostgresStore
    from app.guardrail.classifier import classifier_from_env

    parser = argparse.ArgumentParser()
    parser.add_argument("test_set")
    parser.add_argument("--notes")
    args = parser.parse_args()
    load_dotenv()

    data = yaml.safe_load(open(args.test_set, encoding="utf-8"))
    model, classifier = model_from_env(), classifier_from_env()
    store = PostgresStore(os.environ["DATABASE_URL"], max_size=8)
    try:
        for part in (model, classifier):
            if hasattr(part, "warm_up"):
                part.warm_up()

        def one(case):
            engine = Engine(model, store=store, source="simulator", classifier=classifier)
            turns = play(engine, case)
            return case, turns, grade(case, turns)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(one, data["cases"]))

        em = [r for r in results if "emergency_at" in r[0]]
        hn = [r for r in results if "emergency_at" not in r[0]]
        metrics = {
            "recall": f"{sum(g['passed'] for _, _, g in em)}/{len(em)}",
            "false_alarms": f"{sum(not g['passed'] for _, _, g in hn)}/{len(hn)}",
            "caught_by": {s: sum(g["source"] == s for _, _, g in em if g["passed"]) for s in ("keywords", "classifier")},
            "classifier": classifier.name if classifier else None,
        }
        run_id = f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        with store.pool.connection() as conn, conn.transaction():
            conn.execute(
                "insert into eval_runs (id, test_set, test_set_version, test_set_status, agent_model,"
                " prompt_version, metrics, notes, finished_at) values (%s, %s, %s, %s, %s, %s, %s, %s, now())",
                (run_id, data["test_set"], data["version"], data["status"], model.name, PROMPT_VERSION,
                 Jsonb(metrics), args.notes),
            )
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into eval_results (run_id, case_id, call_id, kind, question, reply, rule_checks, passed)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [(run_id, c["id"], g["call_id"], "must_catch" if "emergency_at" in c else "hard_negative",
                      " | ".join(c["turns"]), "\n".join(t["reply"] for t in turns), Jsonb(g), g["passed"])
                     for c, turns, g in results],
                )
    finally:
        store.close()

    print(f"Run {run_id}: {data['test_set']} v{data['version']}; recall {metrics['recall']}, "
          f"false alarms {metrics['false_alarms']}, caught by {metrics['caught_by']}")
    for c, _, g in results:
        if not g["passed"]:
            kind = "MISSED" if "emergency_at" in c else "false alarm"
            print(f"  {kind} {c['id']} (fired at {g['fired_at']}, {g['source']}): {c['turns'][-1][:90]}")


if __name__ == "__main__":
    main()
