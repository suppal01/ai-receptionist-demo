"""Run a single-turn test set through the agent, apply rule checks, and store the run.

    python -m sim.run_eval testsets/practice_info_v1.yaml [--limit N] [--notes "..."]

Uses AGENT_MODEL and DATABASE_URL (environment or .env). Every case is a new call stored
with source "simulator"; results go to eval_runs / eval_results. The LLM judge runs as a
separate step on a stored run. Calls the model API: practice_info_v1 costs about $0.20.
"""

import argparse
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.agent.engine import Engine
from app.agent.prompts import PROMPT_VERSION
from sim.graders import cited_ids, rule_checks
from sim.testsets import load_test_set


def run_cases(engine: Engine, cases: list[dict]) -> list[dict]:
    results = []
    for case in cases:
        started = time.perf_counter()
        turn = engine.handle("", case["question"])
        results.append(
            {
                "case_id": case["id"],
                "kind": case["kind"],
                "question": case["question"],
                "call_id": turn.call_id,
                "reply": turn.reply,
                "cited": cited_ids(turn.events),
                "rule_checks": rule_checks(case, turn.reply, turn.events),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
        )
    return results


def summarize(results: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    for r in results:
        if all(c["passed"] for c in r["rule_checks"].values()):
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    latencies = sorted(r["latency_ms"] for r in results)
    return {
        "cases": len(results),
        "rules_passed": by_kind,
        "latency_ms_p50": latencies[len(latencies) // 2] if latencies else None,
        "latency_ms_max": latencies[-1] if latencies else None,
    }


def save_run(store, test_set: dict, model_name: str, results: list[dict], notes: str | None) -> str:
    from psycopg.types.json import Jsonb

    run_id = f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    with store.pool.connection() as conn, conn.transaction():
        conn.execute(
            "insert into eval_runs (id, test_set, test_set_version, test_set_status, agent_model,"
            " prompt_version, metrics, notes, finished_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, now())",
            (run_id, test_set["test_set"], test_set["version"], test_set["status"], model_name,
             PROMPT_VERSION, Jsonb(summarize(results)), notes),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "insert into eval_results (run_id, case_id, call_id, kind, question, reply, cited,"
                " rule_checks, latency_ms) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (run_id, r["case_id"], r["call_id"], r["kind"], r["question"], r["reply"],
                     Jsonb(r["cited"]), Jsonb(r["rule_checks"]), r["latency_ms"])
                    for r in results
                ],
            )
    return run_id


def main() -> None:
    import os

    from app.agent.model import model_from_env
    from app.db.store import PostgresStore

    parser = argparse.ArgumentParser()
    parser.add_argument("test_set")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--notes")
    args = parser.parse_args()

    load_dotenv()
    test_set = load_test_set(args.test_set)
    cases = test_set["cases"][: args.limit] if args.limit else test_set["cases"]
    model = model_from_env()
    store = PostgresStore(os.environ["DATABASE_URL"])
    try:
        if hasattr(model, "warm_up"):
            model.warm_up()
        results = run_cases(Engine(model, store=store, source="simulator"), cases)
        run_id = save_run(store, test_set, model.name, results, args.notes)
    finally:
        store.close()

    failed = [r for r in results if not all(c["passed"] for c in r["rule_checks"].values())]
    print(f"\nRun {run_id}: {len(cases)} cases, model {model.name}, prompt {PROMPT_VERSION}")
    print(f"Rule checks: {summarize(results)}")
    for r in failed:
        bad = {k: v["detail"] for k, v in r["rule_checks"].items() if not v["passed"]}
        print(f"  FAIL {r['case_id']}: {bad}\n       reply: {r['reply'][:160]}")


if __name__ == "__main__":
    main()
