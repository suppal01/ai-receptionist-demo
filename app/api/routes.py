"""HTTP routes. Each turn goes through the engine: guardrail first, then the agent."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.agent.engine import Engine
from app.agent.model import model_from_env
from app.api.schemas import FeedbackRequest, TurnRequest, TurnResponse
from app.db.store import store_from_env
from app.guardrail.classifier import classifier_from_env

# Local development reads settings from .env; on Cloud Run there is no .env and the
# environment (including DATABASE_URL from Secret Manager) is set by the service.
load_dotenv()

router = APIRouter()

engine = Engine(model_from_env(), store=store_from_env(), classifier=classifier_from_env())


CHAT_PAGE = Path(__file__).resolve().parents[1] / "templates" / "chat.html"


@router.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def chat() -> HTMLResponse:
    """A browser test call: type as the caller, see each turn's stage, citations and checks."""
    return HTMLResponse(CHAT_PAGE.read_text(encoding="utf-8"))


@router.post("/api/feedback", status_code=201, include_in_schema=False)
def feedback(req: FeedbackRequest) -> dict[str, int]:
    """Flag a receptionist reply from the chat page: the reply is stored with the expectation."""
    fid = engine.store.add_feedback(req.call_id, req.turn, req.expected.strip())
    if fid is None:
        raise HTTPException(status_code=404, detail="No such call or reply.")
    return {"id": fid}


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
