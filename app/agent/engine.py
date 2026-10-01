"""Turn engine: the deterministic code around the agent model (docs/plan.md section 2).

Every turn:
  1. Guardrail (keywords). On an emergency: script + handoff, and the model is not called.
  2. Load call state.
  3. Model interprets the message (intents, fields, draft answer), calling search_kb.
  4. Code checks the draft and decides the next step: grounding, price/clinical declines,
     field validation, stage transitions, scripted read-back and close.
  5. Banned-phrase backstop, save state, return the reply and events.

The model never picks the stage, saves a request, or hands off. Code does.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app import guardrail, kb
from app.agent.checks import (
    asks_about_price,
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

Event = dict[str, Any]


@dataclass
class CallState:
    id: str
    stage: str = "open"
    request_type: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    ended: bool = False


@dataclass
class TurnResult:
    call_id: str
    reply: str
    stage: str
    events: list[Event]
    ended: bool


class Engine:
    def __init__(self, model: AgentModel):
        self.model = model
        # In memory until the database increment.
        self.calls: dict[str, CallState] = {}
        self.requests: dict[str, RequestRecord] = {}

    # --- One turn ---------------------------------------------------------------------------

    def handle(self, call_id: str | None, text: str) -> TurnResult:
        first_turn = not call_id
        call = self.calls.get(call_id) if call_id else None
        if call is None:
            call = CallState(id=call_id or str(uuid.uuid4()))
            self.calls[call.id] = call

        events: list[Event] = []
        parts: list[str] = []

        match = guardrail.check(text)
        events.append(
            {
                "type": "guardrail",
                "payload": {"triggered": match is not None}
                | (
                    {"rule_id": match.rule_id, "severity": match.severity, "phrase": match.phrase}
                    if match
                    else {}
                ),
            }
        )
        if match:
            parts.append(script("emergency"))
            events.append(
                {"type": "handoff", "payload": {"reason": "emergency", "severity": match.severity}}
            )
            call.request_type = "urgent"
            self._move(call, "emergency", events)
        else:
            self._agent_turn(call, text, events, parts)

        reply = " ".join(parts)
        banned = find_banned(reply)
        if banned:
            events.append(_check("banned_phrases", banned))
            reply = script("decline_unknown")
        if first_turn:
            reply = f"{script('disclosure')} {reply}"

        call.history += [{"role": "caller", "text": text}, {"role": "agent", "text": reply}]
        return TurnResult(call.id, reply, call.stage, events, call.ended)

    def _agent_turn(self, call: CallState, text: str, events: list[Event], parts: list[str]):
        returned: dict[str, str] = {}
        searches: list[dict[str, Any]] = []

        def search_kb(query: str) -> list[kb.Hit]:
            hits = kb.search(query)
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
                },
            }
        )
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

        if "goodbye" in intents or ("deny" in intents and call.stage in ("open", "answer", "close")):
            parts.append(script("close_goodbye"))
            call.ended = True
            self._move(call, "close", events)
            return

        changed, invalid_phone = self._merge(call, interp, events)

        if "human" in intents:
            events.append({"type": "handoff", "payload": {"reason": "caller_asked"}})
            call.request_type = call.request_type or "callback"
            self._move(call, "collect", events)
            parts += [p for p in (answer, script("handoff_human")) if p]
            return

        if call.stage not in ("collect", "confirm") and ("request" in intents or changed):
            call.request_type = call.request_type or "new_patient"
            self._move(call, "collect", events)

        if answer:
            parts.append(answer)
        if invalid_phone:
            parts.append(script("invalid_callback"))

        if call.stage == "confirm":
            if changed:
                parts.append(self._read_back(call))
            elif "confirm" in intents:
                parts.append(self._save(call, events))
            elif "deny" in intents:
                parts.append(script("ask_correction"))
            elif not invalid_phone:
                parts.append(self._read_back(call))
        elif call.stage == "collect":
            missing = self._missing(call)
            if invalid_phone:
                pass
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

    def _merge(self, call: CallState, interp: Interpretation, events: list[Event]) -> tuple[bool, bool]:
        """Store valid fields the caller gave. Returns (anything changed, phone was invalid)."""
        changed = invalid_phone = False
        for name in FIELD_ORDER:
            value = (getattr(interp.fields, name) or "").strip()
            if not value:
                continue
            if name == "callback_number":
                phone = normalize_phone(value)
                if phone is None:
                    invalid_phone = True
                    events.append(_check("callback_number", [f"not a 10-digit number: {value!r}"]))
                    continue
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
        record = RequestRecord(call_id=call.id, type=call.request_type, **call.fields)
        self.requests[record.id] = record
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
