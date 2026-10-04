"""Calibrate the judge against human grades.

    python -m sim.calibrate --run RUN_ID GRADES_DIR [--judge gemini-2.5-pro]
    python -m sim.calibrate --seeded sim/calibration/seeded_v1.yaml GRADES_DIR

--run judges a stored eval run (verdicts saved to eval_results.judge, human grades to
eval_results.human_review). --seeded judges the deliberately flawed replies, which test
whether the judge catches failures; nothing is saved for those.

GRADES_DIR holds one JSON file per case ({"case_id", "run_id", "H1": "pass"|"fail", ...}),
as exported from the grading page. The judge is trusted only at >= 90% case agreement
(plan section 5) on both the real and the seeded set.
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
SEEDED_RUN_ID = "seeded-v1"
RUBRIC_PATH = Path("sim/rubric_v2.yaml")


def load_rubric() -> tuple[int, list[dict]]:
    data = yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))
    return data["version"], data["criteria"]


def _cases(path: str | Path) -> dict[str, dict]:
    return {c["id"]: c for c in yaml.safe_load(Path(path).read_text(encoding="utf-8"))["cases"]}


def _item(item_id: str, case: dict, reply: str, cited: list[str]) -> dict:
    kb_text = {e.id: e.text for e in kb.entries()}
    shown = list(dict.fromkeys(list(cited) + case.get("expected_ids", [])))
    return {
        "id": item_id, "kind": case["kind"], "question": case["question"],
        "reply": reply.removeprefix(script("disclosure")).strip(), "cited": list(cited),
        "must_include": case.get("must_include", []), "must_not": case.get("must_not", []),
        "expected_decline": case.get("expected"),
        "kb": {i: kb_text[i] for i in shown if i in kb_text},
    }


def load_items(conn, run_id: str) -> list[dict]:
    name, version = conn.execute(
        "select test_set, test_set_version from eval_runs where id = %s", (run_id,)
    ).fetchone()
    cases = _cases(f"testsets/{name}_v{version}.yaml")
    return [
        _item(case_id, cases[case_id], reply, cited)
        for case_id, reply, cited in conn.execute(
            "select case_id, reply, cited from eval_results where run_id = %s order by case_id", (run_id,)
        )
    ]


def load_seeded(path: str) -> list[dict]:
    """Seeded replies, shown to the judge as if the agent had cited the case's expected entries."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cases = _cases(data["base_test_set"])
    return [
        _item(s["id"], cases[s["case"]], s["reply"], cases[s["case"]].get("expected_ids", []))
        for s in data["items"]
    ]


def load_grades(grades_dir: str, run_id: str) -> dict[str, dict[str, str]]:
    grades = {}
    for f in Path(grades_dir).glob("*.json"):
        doc = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("run_id") == run_id:
            grades[doc["case_id"]] = {c: doc[c] for c in CRITERIA if doc.get(c) in ("pass", "fail")}
    return grades


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run")
    source.add_argument("--seeded")
    parser.add_argument("grades_dir")
    parser.add_argument("--judge", default=os.environ.get("JUDGE_MODEL") or "gemini-2.5-pro")
    args = parser.parse_args()
    load_dotenv()

    rubric_version, rubric = load_rubric()
    store = None
    if args.run:
        from app.db.store import PostgresStore

        store = PostgresStore(os.environ["DATABASE_URL"])
        with store.pool.connection() as conn:
            items = load_items(conn, args.run)
        run_id = args.run
    else:
        items, run_id = load_seeded(args.seeded), SEEDED_RUN_ID

    try:
        human = load_grades(args.grades_dir, run_id)
        judge = GeminiJudge(args.judge)
        with ThreadPoolExecutor(max_workers=6) as pool:
            verdicts = dict(zip([i["id"] for i in items], pool.map(lambda i: judge.judge(i, rubric), items)))
        errors = {k: v["error"] for k, v in verdicts.items() if "error" in v}
        judged = {k: {c: v[c]["verdict"] for c in v} for k, v in verdicts.items() if "error" not in v}
        kinds = {i["id"]: i["kind"] for i in items}
        if store:
            _save(store, run_id, items, verdicts, judged, human, rubric, args.judge, rubric_version)
    finally:
        if store:
            store.close()

    report = agreement(human, judged, rubric, kinds)
    pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"
    print(f"Judge {args.judge} ({JUDGE_PROMPT_VERSION}) on {run_id}: {len(judged)} judged, "
          f"{len(errors)} errors, {len(human)} human-graded")
    print(f"Case agreement: {report['cases_agree']}/{report['cases_total']} = "
          f"{pct(report['cases_agree'], report['cases_total'])}")
    print(f"Criterion agreement: {report['criteria_agree']}/{report['criteria_total']} = "
          f"{pct(report['criteria_agree'], report['criteria_total'])}")
    for d in report["disagreements"]:
        print(f"  {d['case_id']} {d['criterion']}: you={d['human']} judge={d['judge']} | "
              f"judge: {verdicts[d['case_id']][d['criterion']]['reason']}")
    for k, e in errors.items():
        print(f"  ERROR {k}: {e}")


def _save(store, run_id, items, verdicts, judged, human, rubric, judge_model, rubric_version) -> None:
    from psycopg.types.json import Jsonb

    with store.pool.connection() as conn, conn.transaction():
        for i in items:
            v, h = verdicts[i["id"]], human.get(i["id"])
            ok = case_passes(judged[i["id"]], rubric, i["kind"]) if i["id"] in judged else None
            failed = "; ".join(
                f"{c}: {r['reason']}" for c, r in v.items() if isinstance(r, dict) and r["verdict"] == "fail"
            )
            conn.execute(
                "update eval_results set judge = %s, judge_reason = %s, passed = %s, human_review = %s"
                " where run_id = %s and case_id = %s",
                (Jsonb(v | {"_model": judge_model, "_prompt": JUDGE_PROMPT_VERSION}), failed or None,
                 ok, Jsonb(h) if h else None, run_id, i["id"]),
            )
        conn.execute(
            "update eval_runs set judge_model = %s, rubric_version = %s where id = %s",
            (judge_model, rubric_version, run_id),
        )


if __name__ == "__main__":
    main()
