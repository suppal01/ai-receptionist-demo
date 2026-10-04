"""Run a multi-turn test set: a simulated caller talks to the agent; each call is graded.

    python -m sim.run_calls testsets/request_capture_v1.yaml [--limit N] [--notes "..."]

Uses AGENT_MODEL, SIM_MODEL (default gemini-2.5-flash), JUDGE_MODEL (default
gemini-2.5-pro) and DATABASE_URL. Calls are stored with source "simulator"; results go to
eval_runs / eval_results with the transcript, rule checks and judge verdicts.
"""

import argparse
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.agent.engine import Engine
from app.agent.prompts import PROMPT_VERSION
from sim.graders import call_checks
from sim.testsets import load_test_set


def run_call(engine: Engine, caller, case: dict) -> dict:
    """Play one call. The caller opens; the call ends when the agent ends it, the caller
    ends it, or the caller has sent max_turns messages."""
    transcript: list[dict] = []
    call_id, ended_by, text = "", "max_turns", case["caller"]["opening"]
    started = time.perf_counter()
    for turn in range(case["max_turns"]):
        result = engine.handle(call_id, text)
        call_id = result.call_id
        transcript += [{"role": "caller", "text": text, "events": []},
                       {"role": "agent", "text": result.reply, "events": result.events}]
        if result.ended:
            ended_by = "agent"
            break
        if turn == case["max_turns"] - 1:
            break
        nxt = caller.next_message(case, transcript)
        if nxt.end_call and not nxt.text:
            ended_by = "caller"
            break
        text = nxt.text
        if nxt.end_call:
            # Deliver the sign-off, then stop whatever the agent says.
            result = engine.handle(call_id, text)
            transcript += [{"role": "caller", "text": text, "events": []},
                           {"role": "agent", "text": result.reply, "events": result.events}]
            ended_by = "caller"
            break
    saved = [r for r in engine.requests.values() if r.call_id == call_id]
    return {"call_id": call_id, "transcript": transcript, "saved": saved, "ended_by": ended_by,
            "seconds": round(time.perf_counter() - started, 1)}


def transcript_text(transcript: list[dict]) -> str:
    return "\n".join(f"{'CALLER' if t['role'] == 'caller' else 'AGENT'}: {t['text']}" for t in transcript)


def main() -> None:
    from psycopg.types.json import Jsonb

    from app.agent.model import model_from_env
    from app.db.store import PostgresStore
    from sim.calibrate import load_rubric
    from sim.judge import GeminiJudge
    from sim.simulator import SIM_PROMPT_VERSION, GeminiCaller

    parser = argparse.ArgumentParser()
    parser.add_argument("test_set")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--notes")
    args = parser.parse_args()
    load_dotenv()

    test_set = load_test_set(args.test_set)
    cases = test_set["cases"][: args.limit] if args.limit else test_set["cases"]
    model, caller = model_from_env(), GeminiCaller()
    judge = GeminiJudge(os.environ.get("JUDGE_MODEL") or "gemini-2.5-pro")
    rubric_version, rubric = load_rubric()
    store = PostgresStore(os.environ["DATABASE_URL"], max_size=8)
    try:
        if hasattr(model, "warm_up"):
            model.warm_up()

        def one(case):
            engine = Engine(model, store=store, source="simulator")
            call = run_call(engine, caller, case)
            call["case"] = case
            call["rules"] = call_checks(case, call["transcript"], call["saved"])
            call["judge"] = judge.judge_call(case, call["transcript"], call["saved"], rubric)
            return call

        with ThreadPoolExecutor(max_workers=4) as pool:
            calls = list(pool.map(one, cases))
        run_id = _save(store, test_set, model.name, judge.name, rubric_version, calls, args.notes,
                       f"caller {caller.name} ({SIM_PROMPT_VERSION})")
    finally:
        store.close()

    print(f"\nRun {run_id}: {len(calls)} calls, agent {model.name} ({PROMPT_VERSION}), "
          f"caller {caller.name}, judge {judge.name}")
    for c in calls:
        rule_fails = {k: v["detail"] for k, v in c["rules"].items() if not v["passed"]}
        judge_fails = {k: v["reason"] for k, v in c["judge"].items()
                       if isinstance(v, dict) and v.get("verdict") == "fail"}
        status = "PASS" if not rule_fails and not judge_fails and "error" not in c["judge"] else "FAIL"
        print(f"  {status} {c['case']['id']} ({sum(t['role'] == 'caller' for t in c['transcript'])} turns, "
              f"ended by {c['ended_by']}, {c['seconds']}s)")
        for k, v in rule_fails.items():
            print(f"      rule {k}: {v}")
        for k, v in judge_fails.items():
            print(f"      judge {k}: {v}")
        if "error" in c["judge"]:
            print(f"      judge error: {c['judge']['error']}")


def _save(store, test_set, agent_model, judge_model, rubric_version, calls, notes, sim) -> str:
    from psycopg.types.json import Jsonb

    run_id = f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    passed_n = 0
    with store.pool.connection() as conn, conn.transaction():
        rows = []
        for c in calls:
            judge_ok = "error" not in c["judge"] and all(
                v.get("verdict") == "pass" for k, v in c["judge"].items() if isinstance(v, dict) and k != "P2")
            ok = all(v["passed"] for v in c["rules"].values()) and judge_ok
            passed_n += ok
            rows.append((run_id, c["case"]["id"], c["call_id"], "call", c["case"]["caller"]["opening"],
                         transcript_text(c["transcript"]), Jsonb([]), Jsonb(c["rules"]), Jsonb(c["judge"]),
                         ok, round(c["seconds"] * 1000)))
        conn.execute(
            "insert into eval_runs (id, test_set, test_set_version, test_set_status, agent_model, judge_model,"
            " prompt_version, rubric_version, metrics, notes, finished_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
            (run_id, test_set["test_set"], test_set["version"], test_set["status"], agent_model, judge_model,
             PROMPT_VERSION, rubric_version, Jsonb({"calls": len(calls), "passed": passed_n, "simulator": sim}),
             notes),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "insert into eval_results (run_id, case_id, call_id, kind, question, reply, cited, rule_checks,"
                " judge, passed, latency_ms) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
    return run_id


if __name__ == "__main__":
    main()
