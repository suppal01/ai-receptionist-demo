"""Turn engine: the deterministic code around the agent model (docs/plan.md section 2).

Every turn:
  1. Guardrail (keywords). On an emergency: script + handoff, and the model is not called.
  2. Load call state.
  3. Model interprets the message (intents, fields, draft answer), calling search_kb.
  4. Code checks the draft and decides the next step: grounding, price/clinical declines,
     field validation, stage transitions, scripted read-back and close.
  5. Banned-phrase backstop, then record the turn (one write) and log one metadata line.

The model never picks the stage, saves a request, or hands off. Code does.
"""

import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app import guardrail, kb
from app.agent.checks import (
    asks_about_price,
    asks_if_booked,
    is_plain_yes,
    find_banned,
    mentions_price,
    normalize_phone,
    speak_digits,
    unsupported_specifics,
)
from app.agent.model import FIELD_ORDER, AgentModel, Interpretation, TurnContext
from app.agent.prompts import PROMPT_VERSION
from app.agent.request_store import REQUIRED_FIELDS, RequestRecord
from app.agent.scripts import script
from app.agent.state import CallState
from app.db.store import CallStore, MemoryStore
from app.guardrail.classifier import EmergencyClassifier
from app.logs import log

Event = dict[str, Any]

# Classifier calls run beside the agent's model call. How long to wait for its verdict once
# the agent's model has answered (its own request timeout is 8 s).
_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="classifier")
CLASSIFIER_WAIT_S = 8.0


@dataclass
class TurnResult:
    call_id: str
    reply: str
    stage: str
    events: list[Event]
    ended: bool


class Engine:
    def __init__(
        self,
        model: AgentModel,
        store: CallStore | None = None,
        source: str = "api",
        classifier: EmergencyClassifier | None = None,
    ):
        self.model = model
        self.classifier = classifier  # second guardrail layer; None = keywords only
        self.store = store or MemoryStore()
        self.source = source  # "api" for the endpoint, "simulator" for scripted and eval runs
        # Calls this engine has handled; a fallback if the store can't be read.
        self.calls: dict[str, CallState] = {}
        # Requests saved by this engine (also written to the store).
        self.requests: dict[str, RequestRecord] = {}

    # --- One turn ---------------------------------------------------------------------------

    def handle(self, call_id: str | None, text: str) -> TurnResult:
        first_turn = not call_id
        events: list[Event] = []
        parts: list[str] = []
        self._new_requests: list[RequestRecord] = []

        call = self._find_call(call_id, events) if call_id else None
        if call is None:
            call = CallState(id=call_id or str(uuid.uuid4()))
        self.calls[call.id] = call

        match = guardrail.check(text)
        events.append(
            {
                "type": "guardrail",
                "payload": {"source": "keywords", "triggered": match is not None}
                | (
                    {"rule_id": match.rule_id, "severity": match.severity, "phrase": match.phrase}
                    if match
                    else {}
                ),
            }
        )
        if match:
            self._escalate(call, match.severity, "keywords", events, parts)
        else:
            # The classifier reads the message at the same time as the agent's model.
            screening = (
                _POOL.submit(
                    self.classifier.classify, text, list(call.history), call.outcome == "emergency"
                )
                if self.classifier
                else None
            )
            self._agent_turn(call, text, events, parts, screening)

        reply = " ".join(parts)
        banned = find_banned(reply)
        if banned:
            events.append(_check("banned_phrases", banned))
            reply = script("decline_unknown")
        if first_turn:
            reply = f"{script('disclosure')} {reply}"

        call.history += [{"role": "caller", "text": text}, {"role": "agent", "text": reply}]
        self._record(call, text, reply, events)
        return TurnResult(call.id, reply, call.stage, events, call.ended)

    def _escalate(self, call: CallState, severity: str, source: str, events: list[Event], parts: list[str]):
        # Medical: call 911. Dental: urgent callback, no 911 guidance (product owner, 2026-10-05).
        parts.append(script("emergency" if severity == "medical" else "emergency_dental"))
        events.append(
            {"type": "handoff", "payload": {"reason": "emergency", "severity": severity, "source": source}}
        )
        call.request_type = "urgent"
        call.outcome = "emergency"
        self._move(call, "emergency", events)

    def _screened_emergency(self, screening, events: list[Event]):
        """The classifier's verdict, logged as a second guardrail event. None if not an emergency
        (or the classifier failed or was too slow: the keywords already ran)."""
        if screening is None:
            return None
        started = time.perf_counter()
        payload = {"source": "classifier", "model": self.classifier.name, "triggered": False}
        try:
            result = screening.result(timeout=CLASSIFIER_WAIT_S)
            payload |= {"triggered": result.emergency, "severity": result.severity, "reason": result.reason}
        except Exception as e:
            payload["error"] = f"{type(e).__name__}: {e}"
            result = None
        payload["waited_ms"] = round((time.perf_counter() - started) * 1000)
        events.append({"type": "guardrail", "payload": payload})
        return result if result and result.emergency else None

    def _find_call(self, call_id: str, events: list[Event]) -> CallState | None:
        """The store is the source of truth: another instance may have handled the last turn.
        The in-memory copy is only a fallback when the store can't be read."""
        try:
            stored = self.store.load_call(call_id)
            if stored is not None:
                return stored
        except Exception as e:
            events.append({"type": "storage", "payload": {"op": "load", "error": str(e)}})
            log("ERROR", "storage load failed", call_id=call_id, error=str(e))
        return self.calls.get(call_id)

    def _record(self, call: CallState, text: str, reply: str, events: list[Event]) -> None:
        """Write the turn to the store, then log it. A storage failure never blocks the reply."""
        try:
            self.store.record_turn(
                call, text, reply, events, self._new_requests,
                agent_model=self.model.name, prompt_version=PROMPT_VERSION, source=self.source,
            )
        except Exception as e:
            events.append({"type": "storage", "payload": {"op": "record", "error": str(e)}})
            log("ERROR", "storage record failed", call_id=call.id, error=str(e))
        model = next((e["payload"] for e in events if e["type"] == "model"), {})
        log(
            "INFO",
            "turn",
            call_id=call.id,
            seq=len(call.history) // 2,
            stage=call.stage,
            outcome=call.outcome,
            ended=call.ended,
            guardrail_triggered=any(e["payload"].get("triggered") for e in events if e["type"] == "guardrail"),
            model=model.get("model"),
            model_ms=model.get("ms"),
            model_error=model.get("error"),
            intents=model.get("intents"),
            usage=model.get("usage"),
            failed_checks=[
                e["payload"]["check"] for e in events if e["type"] == "check" and not e["payload"]["passed"]
            ],
            requests_saved=[r.id for r in self._new_requests],
        )

    def _agent_turn(self, call: CallState, text: str, events: list[Event], parts: list[str], screening=None):
        returned: dict[str, str] = {}
        searches: list[dict[str, Any]] = []

        def search_kb(query: str, keyword_query: bool = True) -> list[kb.Hit]:
            hits = kb.search(query, keyword_query=keyword_query)
            searches.append({"query": query, "hits": hits})
            returned.update({h.id: h.text for h in hits})
            return hits

        ctx = TurnContext(
            text=text, stage=call.stage, missing_fields=self._missing(call), history=list(call.history)
        )
        started = time.perf_counter()
        interp = self.model.interpret(ctx, search_kb)
        events.append(
            {
                "type": "model",
                "payload": {
                    "model": self.model.name,
                    "prompt_version": PROMPT_VERSION,
                    "ms": round((time.perf_counter() - started) * 1000),
                    "intents": list(interp.intents),
                    "error": interp.error,
                    "usage": interp.usage,
                },
            }
        )
        # Before the agent's interpretation changes anything, hear the classifier out.
        flagged = self._screened_emergency(screening, events)
        if flagged:
            self._escalate(call, flagged.severity, "classifier", events, parts)
            return
        intents = set(interp.intents)
        if asks_about_price(text) and "price" not in intents:
            intents.add("price")
            events.append({"type": "route", "payload": {"reason": "price_keyword"}})

        # Answer part: fixed declines win over the model's draft.
        answer, cited = None, []
        if "price" in intents:
            answer = script("decline_price")
        elif "clinical" in intents:
            answer = script("decline_clinical")
        elif asks_if_booked(text):
            answer = script("clarify_not_booked")
        elif "question" in intents:
            failures = self._verify(interp, returned, call)
            events.append(_check("grounding", failures))
            answer = script("decline_unknown") if failures else interp.draft_reply
            cited = [] if failures else interp.cited_ids
        for s in searches:
            ids = [h.id for h in s["hits"]]
            events.append(
                {
                    "type": "tool_call",
                    "payload": {
                        "tool": "search_kb",
                        "query": s["query"],
                        "results": [{"id": h.id, "score": h.score} for h in s["hits"]],
                        "cited": [c for c in cited if c in ids],
                    },
                }
            )

        confirming_and_leaving = call.stage == "confirm" and "confirm" in intents
        if not confirming_and_leaving and (
            "goodbye" in intents or ("deny" in intents and call.stage in ("open", "answer", "close"))
        ):
            parts.append(script("close_goodbye"))
            call.ended = True
            call.outcome = call.outcome or "info_only"
            self._move(call, "close", events)
            return

        if interp.insurance_asked_about and not call.fields.get("insurance_carrier"):
            call.fields[INSURANCE_ASKED] = interp.insurance_asked_about.strip()

        before = dict(call.fields)
        changed, invalid_phone = self._merge(call, interp, events, correcting="correction" in intents)
        if not changed and not invalid_phone:
            changed = self._answer_to_insurance_confirmation(call, text, intents, events)
        if not changed and not invalid_phone and intents <= {"other", "provide_info"}:
            changed = self._take_plain_answer(call, text, events)
        # Contact details mean the caller wants something followed up. Insurance, times or a
        # reason mentioned inside a question are remembered but don't start a request.
        gave_contact = any(call.fields.get(f) != before.get(f) for f in ("name", "callback_number"))

        handed_off = "human" in intents
        if handed_off:
            events.append({"type": "handoff", "payload": {"reason": "caller_asked"}})
            # A person will call back: only name and number are needed now.
            if call.request_type != "urgent":
                call.request_type = "callback"
            if call.outcome != "emergency":
                call.outcome = "handoff"
            if call.stage != "collect":
                self._move(call, "collect", events)

        if call.stage not in ("collect", "confirm") and ("request" in intents or gave_contact or invalid_phone):
            call.request_type = call.request_type or "new_patient"
            self._move(call, "collect", events)

        if answer:
            parts.append(answer)
        if handed_off:
            parts.append(script("handoff_human"))
        if invalid_phone:
            parts.append(script("invalid_callback"))

        if call.stage == "close" and changed and call.request_type and not self._missing(call):
            # A change after the save (chat call 4f45a5ed): read it back; confirming updates
            # the same request.
            self._move(call, "confirm", events)
            parts.append(self._read_back(call))
        elif call.stage == "confirm":
            if changed:
                parts.append(self._read_back(call))
            elif invalid_phone or "correction" in intents:
                # Never save on a turn that corrects something or has a bad number (rc-004).
                if not invalid_phone:
                    parts.append(self._read_back(call))
            elif "confirm" in intents or (
                # Backstop: a bare "yes, that's right" the model mislabeled (rc-010).
                not intents & {"deny", "correction", "question"} and is_plain_yes(text)
            ):
                parts.append(self._save(call, events))
                if "goodbye" in intents:
                    # "Yes, that's correct. No, that's all, thanks." (rc-016): save, then close.
                    parts.append(script("close_goodbye"))
                    call.ended = True
            elif "deny" in intents:
                parts.append(script("ask_correction"))
            elif not invalid_phone:
                parts.append(self._read_back(call))
        elif call.stage == "collect":
            missing = self._missing(call)
            if invalid_phone:
                pass
            elif missing and missing[0] == "insurance_carrier" and call.fields.get(INSURANCE_ASKED):
                parts.append(script("confirm_insurance", insurance=call.fields[INSURANCE_ASKED]))
            elif missing:
                parts.append(script(f"ask_{missing[0]}"))
            else:
                self._move(call, "confirm", events)
                parts.append(self._read_back(call))
        elif answer:
            parts.append(script("ask_anything_else"))
            if call.stage == "open":
                self._move(call, "answer", events)
        else:
            parts.append(script("ask_how_help"))

    # --- Helpers ----------------------------------------------------------------------------

    def _verify(self, interp: Interpretation, returned: dict[str, str], call: CallState) -> list[str]:
        """Grounding checks on a draft answer. An empty list means it may be sent."""
        failures = []
        draft = interp.draft_reply.strip()
        if not returned:
            failures.append("no search_kb results this turn")
        if not draft:
            failures.append("empty draft")
        if not interp.cited_ids:
            failures.append("no citation")
        unknown = [c for c in interp.cited_ids if c not in returned]
        if unknown:
            failures.append(f"cited entries not returned by search: {', '.join(unknown)}")
        sources = [returned[c] for c in interp.cited_ids if c in returned]
        specifics = unsupported_specifics(draft, sources, allowed=call.fields.values())
        if specifics:
            failures.insert(0, f"unsupported specifics: {', '.join(specifics)}")
        if mentions_price(draft):
            failures.append("states a price")
        banned = find_banned(draft)
        if banned:
            failures.append(f"banned phrases: {', '.join(banned)}")
        return failures

    def _answer_to_insurance_confirmation(self, call: CallState, text: str, intents: set, events) -> bool:
        """After "Earlier you asked about X. Is that the plan you have?": yes records X; no drops
        it so the normal insurance question follows."""
        asked = call.fields.get(INSURANCE_ASKED)
        last = next((h["text"] for h in reversed(call.history) if h["role"] == "agent"), "")
        if not asked or script("confirm_insurance", insurance=asked) not in last:
            return False
        if "confirm" in intents or is_plain_yes(text):
            call.fields["insurance_carrier"] = asked
            call.fields.pop(INSURANCE_ASKED, None)
            events.append({"type": "route", "payload": {"reason": "insurance_confirmed"}})
            return True
        call.fields.pop(INSURANCE_ASKED, None)
        return False

    def _take_plain_answer(self, call: CallState, text: str, events: list[Event]) -> bool:
        """The caller answered the question just asked, but the model extracted nothing.

        Only for free-text details, and only when the last reply asked for exactly that one.
        Never for name or phone, which must come from the model and pass validation.
        """
        last = next((h["text"] for h in reversed(call.history) if h["role"] == "agent"), "")
        answer = text.strip()
        if call.stage != "collect" or "?" in answer or not answer or len(answer) > 200:
            return False
        for name in PLAIN_ANSWER_FIELDS:
            if script(f"ask_{name}") in last and not call.fields.get(name):
                call.fields[name] = answer
                events.append({"type": "route", "payload": {"reason": "answer_to_last_question", "field": name}})
                return True
        return False

    def _merge(
        self, call: CallState, interp: Interpretation, events: list[Event], correcting: bool = False
    ) -> tuple[bool, bool]:
        """Store valid fields the caller gave. Returns (anything changed, phone was invalid)."""
        changed = invalid_phone = False
        for name in FIELD_ORDER:
            value = (getattr(interp.fields, name) or "").strip()
            if not value:
                continue
            if name == "name":
                # Asides belong in the transcript, not the name ("Leo Park (mom: Dana)",
                # "Leo Park, and I'm his mom, Dana": rc-013).
                value = re.sub(r"\s*\([^)]*\)", "", value).split(",")[0].strip()
                if not value:
                    continue
            if name == "reason_for_visit" and _is_generic_reason(value):
                events.append(_check("reason_for_visit", [f"too general to record: {value!r}"]))
                continue
            if name == "callback_number":
                phone = normalize_phone(value)
                if phone is None:
                    digits = re.sub(r"\D", "", value)
                    known = call.fields.get("callback_number")
                    if known and 0 < len(digits) < 10 and correcting:
                        # A correction of the end of a number we already have ("the last four
                        # digits are actually 0174", rc-004): replace those digits.
                        phone = known[: 10 - len(digits)] + digits
                    else:
                        # A number given in pieces ("503 555..." then "0167", rc-003): join them.
                        phone = normalize_phone(call.fields.get(PARTIAL_PHONE, "") + digits)
                    if phone is None:
                        if 0 < len(digits) < 10:
                            call.fields[PARTIAL_PHONE] = (call.fields.get(PARTIAL_PHONE, "") + digits)[-9:]
                        invalid_phone = True
                        events.append(_check("callback_number", [f"not a 10-digit number: {value!r}"]))
                        continue
                call.fields.pop(PARTIAL_PHONE, None)
                value = phone
            if call.fields.get(name) != value:
                call.fields[name] = value
                changed = True
        return changed, invalid_phone

    def _missing(self, call: CallState) -> list[str]:
        required = REQUIRED_FIELDS[call.request_type or "new_patient"]
        return [f for f in required if not call.fields.get(f)]

    def _read_back(self, call: CallState) -> str:
        spoken = speak_digits(call.fields["callback_number"])
        if call.request_type != "new_patient":
            return script("readback_callback", digits_spoken=spoken)
        insurance = call.fields["insurance_carrier"]
        return script(
            "readback_request",
            name=call.fields["name"],
            digits_spoken=spoken,
            preferred_times=_mid_sentence(call.fields["preferred_times"]),
            insurance_or_none="no insurance" if insurance.lower() in ("none", "no") else insurance,
            reason_for_visit=_mid_sentence(call.fields["reason_for_visit"]),
        )

    def _save(self, call: CallState, events: list[Event]) -> str:
        details = {k: v for k, v in call.fields.items() if not k.startswith("_")}
        # One request per call: saving again after a change updates it instead of duplicating.
        record = RequestRecord(
            id=f"req-{call.id.replace('-', '')[:12]}", call_id=call.id, type=call.request_type, **details
        )
        self.requests[record.id] = record
        self._new_requests.append(record)
        call.outcome = call.outcome or "request_saved"
        events.append(
            {
                "type": "tool_call",
                "payload": {"tool": "save_request", "request_id": record.id, "request_type": record.type},
            }
        )
        self._move(call, "close", events)
        return script(
            "close_request_saved",
            first_name=record.name.split()[0],
            digits_spoken=speak_digits(record.callback_number),
        )

    @staticmethod
    def _move(call: CallState, to: str, events: list[Event]):
        if call.stage != to:
            events.append({"type": "route", "payload": {"from": call.stage, "to": to}})
            call.stage = to


PLAIN_ANSWER_FIELDS = ("preferred_times", "insurance_carrier", "reason_for_visit")
# Digits of a phone number given in pieces, kept until the rest arrives (never saved).
PARTIAL_PHONE = "_partial_phone"
# A plan the caller asked about ("Do you take Patriot?"), to confirm later (never saved).
INSURANCE_ASKED = "_insurance_asked"

# A reason made only of these words says nothing about why the caller is coming in
# ("first visit", "new patient appointment", "son's first visit").
_GENERIC_REASON_WORDS = {
    "a", "an", "the", "my", "his", "her", "their", "our", "son's", "daughter's", "child's", "kid's",
    "new", "patient", "first", "initial", "visit", "appointment", "consultation", "consult", "to",
    "come", "in", "become", "get", "started", "start", "for", "be", "seen",
    # Whose visit it is says nothing about why ("first visit for son", rc-013).
    "son", "daughter", "child", "kid", "kids", "wife", "husband", "mom", "dad", "family", "me",
}


def _is_generic_reason(value: str) -> bool:
    words = re.findall(r"[a-z']+", value.lower().replace("’", "'"))
    return all(w in _GENERIC_REASON_WORDS for w in words)


_PROPER_FIRST_WORDS = {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}


def _mid_sentence(value: str) -> str:
    """'Weekday mornings' reads as '... weekday mornings'; days, months and acronyms keep caps."""
    first = value.split()[0] if value.split() else ""
    if first[:1].isupper() and not first.isupper() and first not in _PROPER_FIRST_WORDS:
        return value[0].lower() + value[1:]
    return value


def _check(name: str, failures: list[str]) -> Event:
    return {"type": "check", "payload": {"check": name, "passed": not failures, "failures": failures}}
