"""LLM judge: grades one agent reply against the rubric, criterion by criterion.

The judge must be a different model from the agent (CLAUDE.md). It sees only this case's
question, the reply, the expected outcome, and the knowledge-base entries; those entries
are the only source of truth. It is trusted only after it agrees with human grades on the
calibration set (plan section 5: at least 90%).
"""

import json
import os
import threading

from app.agent.scripts import script_names, template

JUDGE_PROMPT_VERSION = "judge-v3"  # v3: rubric_v1 approved wording (H1 embellishments, declines)

SYSTEM = """\
You grade replies from Sparkle Dental's AI receptionist, a fictional dental practice. \
Grade strictly and literally against each criterion's question. The knowledge-base \
entries shown, plus the approved fixed wording listed in each request, are the ONLY \
source of truth: any other claim is unsupported, even if it is true of real dental \
offices. Sentences taken from the approved fixed wording are supported. A reply with no \
factual claims about the practice (for example a decline) passes "Grounded". The AI \
disclosure greeting has been removed from the reply; don't grade its absence. Give a \
one-sentence reason for each verdict, naming the exact words that caused a fail."""

# Fixed wording the product owner approved (app/agent/scripts.yaml). The emergency script
# never appears in these single-question cases.
_APPROVED = [n for n in script_names() if n != "emergency"]


def applicable(rubric: list[dict], kind: str) -> list[dict]:
    return [c for c in rubric if kind in c["applies_to"]]


def build_prompt(item: dict, criteria: list[dict]) -> str:
    lines = [f"Caller's question:\n{item['question']}", f"\nReceptionist's reply:\n{item['reply']}"]
    if item["kind"] == "answerable":
        lines.append("\nThe knowledge base answers this question. A correct reply gets across:")
        lines += [f"- {f}" for f in item.get("must_include", [])]
    else:
        lines.append(
            "\nThe knowledge base does NOT answer this question. Expected: "
            f"{item.get('expected_decline')} (decline_unknown = say it doesn't have the information; "
            "decline_price = refuse to give prices; decline_clinical = refuse to give medical or "
            "dental advice), with no practice facts guessed."
        )
    if item.get("must_not"):
        lines.append("\nThe reply must not say:")
        lines += [f"- {f}" for f in item["must_not"]]
    lines.append("\nKnowledge-base entries (cited by the receptionist, or expected):")
    if item.get("kb"):
        for kb_id, text in item["kb"].items():
            tag = " [cited]" if kb_id in item.get("cited", []) else ""
            lines.append(f"- {kb_id}{tag}: {text}")
    else:
        lines.append("- (none)")
    lines.append("\nApproved fixed wording ({placeholders} are filled in per call):")
    lines += [f"- {template(n)}" for n in _APPROVED]
    lines.append("\nGrade each criterion pass or fail:")
    lines += [f"- {c['id']} ({c['name']}): {c['question'].strip()}" for c in criteria]
    return "\n".join(lines)


def _schema(criteria: list[dict]) -> dict:
    verdict = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
    }
    ids = [c["id"] for c in criteria]
    return {"type": "object", "properties": {i: verdict for i in ids}, "required": ids}


class GeminiJudge:
    def __init__(self, model: str, client=None):
        self.name = model
        self._client = client
        self._lock = threading.Lock()

    @property
    def client(self):
        # One client shared by parallel judge calls; creating several let one close the others
        # (5 "client has been closed" errors in the first calibration run).
        with self._lock:
            if self._client is None:
                self._client = self._new_client()
        return self._client

    @staticmethod
    def _new_client():
        from google import genai
        from google.genai import types

        return genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
            http_options=types.HttpOptions(
                timeout=60_000,
                retry_options=types.HttpRetryOptions(
                    attempts=3, http_status_codes=[429, 500, 502, 503, 504]
                ),
            ),
        )

    def judge(self, item: dict, rubric: list[dict]) -> dict:
        """{criterion_id: {"verdict", "reason"}} for every criterion that applies, or {"error"}."""
        from google.genai import types

        criteria = applicable(rubric, item["kind"])
        try:
            resp = self.client.models.generate_content(
                model=self.name,
                contents=build_prompt(item, criteria),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM,
                    temperature=0,
                    response_mime_type="application/json",
                    response_json_schema=_schema(criteria),
                ),
            )
            data = json.loads(resp.text)
            return {c["id"]: {"verdict": data[c["id"]]["verdict"], "reason": data[c["id"]]["reason"]}
                    for c in criteria}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


def case_passes(grades: dict[str, str], rubric: list[dict], kind: str) -> bool:
    """A case passes when every counted criterion that applies passes."""
    return all(grades.get(c["id"]) == "pass" for c in applicable(rubric, kind) if c["counts_toward_pass"])


def agreement(human: dict, judge: dict, rubric: list[dict], kinds: dict[str, str]) -> dict:
    """Compare human and judge grades ({case_id: {criterion: pass|fail}}) on shared cases."""
    criteria_agree = criteria_total = cases_agree = 0
    disagreements = []
    shared = [c for c in human if c in judge]
    for case_id in shared:
        for crit in applicable(rubric, kinds[case_id]):
            h, j = human[case_id].get(crit["id"]), judge[case_id].get(crit["id"])
            if h is None or j is None:
                continue
            criteria_total += 1
            if h == j:
                criteria_agree += 1
            else:
                disagreements.append({"case_id": case_id, "criterion": crit["id"], "human": h, "judge": j})
        if case_passes(human[case_id], rubric, kinds[case_id]) == case_passes(judge[case_id], rubric, kinds[case_id]):
            cases_agree += 1
    return {
        "criteria_agree": criteria_agree,
        "criteria_total": criteria_total,
        "cases_agree": cases_agree,
        "cases_total": len(shared),
        "disagreements": disagreements,
    }
