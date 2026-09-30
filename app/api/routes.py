"""HTTP routes. Each turn runs the guardrail first, then the agent (docs/plan.md section 2)."""

import uuid

from fastapi import APIRouter

from app import agent, guardrail
from app.agent.scripts import script
from app.api.schemas import TurnRequest, TurnResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check for local runs and Cloud Run."""
    return {"status": "ok"}


@router.post("/api/turn", response_model=TurnResponse)
def turn(req: TurnRequest) -> TurnResponse:
    """Handle one caller message. An empty call_id starts a new call."""
    first_turn = not req.call_id
    call_id = req.call_id or str(uuid.uuid4())

    match = guardrail.check(req.text)
    guard_event = {
        "type": "guardrail",
        "payload": {"triggered": match is not None}
        | ({"rule_id": match.rule_id, "severity": match.severity, "phrase": match.phrase} if match else {}),
    }

    if match:
        reply, stage = script("emergency"), "emergency"
        events = [
            guard_event,
            {"type": "handoff", "payload": {"reason": "emergency", "severity": match.severity}},
        ]
    else:
        reply, stage, agent_events = agent.respond(req.text)
        events = [guard_event, *agent_events]

    if first_turn:
        reply = f"{script('disclosure')} {reply}"

    return TurnResponse(call_id=call_id, reply=reply, stage=stage, events=events, ended=False)
