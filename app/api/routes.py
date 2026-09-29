"""HTTP routes. The turn handler is a placeholder until the agent is built."""

import uuid

from fastapi import APIRouter

from app.api.schemas import TurnRequest, TurnResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check for local runs and Cloud Run."""
    return {"status": "ok"}


@router.post("/api/turn", response_model=TurnResponse)
def turn(req: TurnRequest) -> TurnResponse:
    """Placeholder: echo the caller's text back. The agent replaces this later."""
    call_id = req.call_id or str(uuid.uuid4())
    return TurnResponse(
        call_id=call_id,
        reply=f"You said: {req.text}",
        stage="echo",
        events=[{"type": "echo", "payload": {"chars": len(req.text)}}],
        ended=False,
    )
