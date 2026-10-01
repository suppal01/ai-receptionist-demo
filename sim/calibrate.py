"""Calibrate the judge: run it on a stored eval run and compare with human grades.

    python -m sim.calibrate RUN_ID GRADES_DIR [--judge gemini-2.5-pro]

GRADES_DIR holds one JSON file per case ({"case_id", "run_id", "H1": "pass"|"fail", ...}),
as exported from the grading page. Judge verdicts go to eval_results.judge and the human
grades to eval_results.human_review. The judge is trusted only at >= 90% case agreement
(plan section 5), and only if the calibration set includes failures for it to catch.
"""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from dotenv import load_dotenv

from app import kb
from app.agent.scripts import script
from sim.judge import JUDGE_PROMPT_VERSION, GeminiJudge, agreement, case_passes

CRITERIA = ("H1", "H2", "P1", "P2", "S1", "S2")


def load_items(conn, run_id: str) -> list[dict]:
    test_set = conn.execute("select test_set, test_set_version from eval_runs where id = %s", (run_id,)).fetchone()
    cases = {c["id"]: c for c in yaml.safe_load(
        Path(f"testsets/{test_set[0]}_v{test_set[1]}.yaml").read_text(encoding="utf-8"))["cases"]}
    kb_text = {e.id: e.text for e in kb.entries()}
    disclosure = script("disclosure")
    items = []
    for case_id, kind, question, reply, cited in conn.execute(
        "select case_id, kind, question, reply, cited from eval_results where run_id = %s order by case_id",
        (run_id,),
    ):
        case = cases[case_id]
        shown = list(dict.fromkeys(list(cited) + case.get("expected_ids", [])))
        items.append({
            "id": case_id, "kind": kind, "question": question,
            "reply": reply.removeprefix(disclosure).strip(), "cited": list(cited),
            "must_include": case.get("must_include", []), "must_not": case.get("must_not", []),
            "expected_decline": case.get("expected"),
            "kb": {i: kb_text[i] for i in shown if i in kb_text},
        })
    return items


def load_grades(grades_dir: str, run_id: str) -> dict[str, dict[str, str]]:
    grades = {}
    for f in Path(grades_dir).glob("*.json"):
        doc = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("run_id") == run_id:
            grades[doc["case_id"]] = {c: doc[c] for c in CRITERIA if doc.get(c) in ("pass", "fail")}
    return grades


def main() -> None:
    from psycopg.types.json import Jsonb

    from app.db.store import PostgresStore

    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("grades_dir")
    parser.add_argument("--judge", default="gemini-2.5-pro")
    args = parser.parse_args()
    load_dotenv()

    rubric = yaml.safe_load(Path("sim/rubric_v1.yaml").read_text(encoding="utf-8"))["criteria"]
    store = PostgresStore(os.environ["DATABASE_URL"])
    try:
        with store.pool.connection() as conn:
            items = load_items(conn, args.run_id)
        human = load_grades(args.grades_dir, args.run_id)
        judge = GeminiJudge(args.judge)
        with ThreadPoolExecutor(max_workers=6) as pool:
            verdicts = dict(zip([i["id"] for i in items], pool.map(lambda i: judge.judge(i, rubric), items)))
        errors = {k: v["error"] for k, v in verdicts.items() if "error" in v}
        judged = {k: {c: v[c]["verdict"] for c in v} for k, v in verdicts.items() if "error" not in v}
        kinds = {i["id"]: i["kind"] for i in items}

        with store.pool.connection() as conn, conn.transaction():
            for i in items:
                v, h = verdicts[i["id"]], human.get(i["id"])
                ok = case_passes(judged[i["id"]], rubric, i["kind"]) if i["id"] in judged else None
                conn.execute(
                    "update eval_results set judge = %s, judge_reason = %s, passed = %s, human_review = %s"
                    " where run_id = %s and case_id = %s",
                    (Jsonb(v | {"_model": args.judge, "_prompt": JUDGE_PROMPT_VERSION}),
                     "; ".join(f"{c}: {r['reason']}" for c, r in v.items() if isinstance(r, dict) and r["verdict"] == "fail") or None,
                     ok, Jsonb(h) if h else None, args.run_id, i["id"]),
                )
            conn.execute("update eval_runs set judge_model = %s, rubric_version = 1 where id = %s", (args.judge, args.run_id))
    finally:
        store.close()

    report = agreement(human, judged, rubric, kinds)
    pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"
    print(f"Judge {args.judge} ({JUDGE_PROMPT_VERSION}) on {args.run_id}: {len(judged)} judged, {len(errors)} errors")
    print(f"Case agreement: {report['cases_agree']}/{report['cases_total']} = {pct(report['cases_agree'], report['cases_total'])}")
    print(f"Criterion agreement: {report['criteria_agree']}/{report['criteria_total']} = "
          f"{pct(report['criteria_agree'], report['criteria_total'])}")
    for d in report["disagreements"]:
        reason = verdicts[d["case_id"]][d["criterion"]]["reason"]
        print(f"  {d['case_id']} {d['criterion']}: you={d['human']} judge={d['judge']} | judge: {reason}")
    for k, e in errors.items():
        print(f"  ERROR {k}: {e}")


if __name__ == "__main__":
    main()
