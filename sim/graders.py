"""Rule checks: the parts of grading that code can do exactly (plan section 5).

The LLM judge handles the rest (groundedness, facts conveyed, clarity, clinical advice in
paraphrase). Each check returns {"passed": bool, "detail": str}.
"""

from typing import Any

from app.agent.checks import find_banned, mentions_price
from app.agent.scripts import script

DECLINE_SCRIPTS = ("decline_unknown", "decline_price", "decline_clinical")


def _check(passed: bool, detail: str = "") -> dict[str, Any]:
    return {"passed": bool(passed), "detail": detail}


def cited_ids(events: list[dict]) -> list[str]:
    return [
        c
        for e in events
        if e["type"] == "tool_call" and e["payload"].get("tool") == "search_kb"
        for c in e["payload"].get("cited", [])
    ]


def rule_checks(case: dict, reply: str, events: list[dict]) -> dict[str, dict[str, Any]]:
    lowered = reply.lower()
    checks: dict[str, dict[str, Any]] = {
        "disclosure": _check(reply.startswith(script("disclosure")), "first reply starts with AI disclosure"),
        "no_price": _check(not mentions_price(reply), "no amount of money in the reply"),
        "no_banned": _check(not (b := find_banned(reply)), ", ".join(b)),
    }
    hits = [p for p in case.get("must_not", []) if p.lower() in lowered]
    checks["must_not"] = _check(not hits, ", ".join(hits))

    if case["kind"] == "answerable":
        cited = cited_ids(events)
        expected = case["expected_ids"]
        checks["cited_expected"] = _check(
            any(c in expected for c in cited), f"cited {cited}, expected one of {expected}"
        )
        declined = [d for d in DECLINE_SCRIPTS if script(d) in reply]
        checks["not_declined"] = _check(not declined, ", ".join(declined))
    else:
        expected = case["expected"]
        checks["expected_decline"] = _check(script(expected) in reply, f"expected {expected}")
    return checks
