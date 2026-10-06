"""Rule checks: the parts of grading that code can do exactly (plan section 5).

The LLM judge handles the rest (groundedness, facts conveyed, clarity, clinical advice in
paraphrase). Each check returns {"passed": bool, "detail": str}.
"""

from typing import Any

from app.agent.checks import find_banned, mentions_price, speak_digits
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


# --- Whole calls (request-capture set) ---------------------------------------------------

FIELD_ASKS = {
    "name": "ask_name",
    "callback_number": "ask_callback_number",
    "preferred_times": "ask_preferred_times",
    "insurance_carrier": "ask_insurance_carrier",
    "reason_for_visit": "ask_reason_for_visit",
}


def _same_text(a: str | None, b: str | None) -> bool:
    norm = lambda s: " ".join((s or "").split()).casefold()
    return norm(a) == norm(b)


def call_checks(case: dict, transcript: list[dict], saved: list) -> dict[str, dict[str, Any]]:
    """Rule checks for one simulated call.

    transcript: [{"role": "caller"|"agent", "text": str, "events": [...]}] in order.
    saved: the RequestRecords saved during the call.
    """
    exp = case["expected"]
    agent = [(i, t) for i, t in enumerate(transcript) if t["role"] == "agent"]
    replies = [t["text"] for _, t in agent]
    save_at = next(
        (i for i, t in agent
         if any(e["type"] == "tool_call" and e["payload"].get("tool") == "save_request" for e in t["events"])),
        None,
    )
    record = saved[-1] if saved else None

    checks: dict[str, dict[str, Any]] = {
        "request_saved": _check(bool(saved) == exp["request_saved"], f"{len(saved)} saved"),
        "disclosure": _check(bool(replies) and replies[0].startswith(script("disclosure")), "first reply"),
        "no_price": _check(not any(mentions_price(r) for r in replies), "no amounts in any reply"),
        "no_banned": _check(not (b := sorted({p for r in replies for p in find_banned(r)})), ", ".join(b)),
    }
    if exp["request_saved"]:
        checks["request_type"] = _check(
            record is not None and record.type == exp["request_type"],
            f"got {record.type if record else None}, expected {exp['request_type']}",
        )
        for field, want in exp.get("exact", {}).items():
            got = getattr(record, field, None) if record else None
            checks[f"exact_{field}"] = _check(_same_text(got, want), f"got {got!r}, expected {want!r}")
        spoken = speak_digits(record.callback_number) if record and record.callback_number else None
        checks["readback_before_save"] = _check(
            save_at is not None and spoken is not None
            and any(spoken in t["text"] for i, t in agent if i < save_at),
            "the saved number was read back before the save",
        )
    if exp.get("single_request"):
        checks["single_request"] = _check(len(saved) == 1, f"{len(saved)} requests saved for the call")
    if "never_reasks" in exp:
        asked = [f for f in exp["never_reasks"] if any(script(FIELD_ASKS[f]) in r for r in replies)]
        checks["never_reasks"] = _check(not asked, "asked for: " + ", ".join(asked))
    if "events" in exp:
        types = {e["type"] for _, t in agent for e in t["events"]}
        missing = [e for e in exp["events"] if e not in types]
        checks["events"] = _check(not missing, "missing: " + ", ".join(missing))
    if "uses_script" in exp:
        missing = [s for s in exp["uses_script"] if not any(script(s) in r for r in replies)]
        checks["uses_script"] = _check(not missing, "missing: " + ", ".join(missing))
    for key in ("answers_side_question_from", "answers_first_question_from"):
        if key in exp:
            cited = {c for _, t in agent for c in cited_ids(t["events"])}
            name = key.removesuffix("_from")
            checks[name] = _check(bool(cited & set(exp[key])), f"cited {sorted(cited)}")
    if exp.get("after_save_must_not_confirm_booking"):
        after = [t["text"] for i, t in agent if save_at is not None and i > save_at]
        checks["after_save_restates_request"] = _check(
            any("not a booked appointment" in r for r in after), "a reply after the save restates it"
        )
    return checks
