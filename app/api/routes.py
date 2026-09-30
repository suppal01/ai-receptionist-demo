"""HTTP routes. Each turn goes through the engine: guardrail first, then the agent."""

from fastapi import APIRouter

from app.agent.engine import Engine
from app.agent.model import model_from_env
from app.api.schemas import TurnRequest, TurnResponse

router = APIRouter()

engine = Engine(model_from_env())


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check for local runs and Cloud Run."""
    return {"status": "ok"}


@router.post("/api/turn", response_model=TurnResponse)
def turn(req: TurnRequest) -> TurnResponse:
    """Handle one caller message. An empty call_id starts a new call."""
    result = engine.handle(req.call_id, req.text)
    return TurnResponse(
        call_id=result.call_id,
        reply=result.reply,
        stage=result.stage,
        events=result.events,
        ended=result.ended,
    )
